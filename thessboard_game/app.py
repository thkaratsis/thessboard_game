from __future__ import annotations

import json
import random
import string
from collections import deque
from pathlib import Path
from typing import Any

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room

BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "static" / "data" / "game_data.json"
GAME_DATA = json.loads(DATA_PATH.read_text(encoding="utf-8"))

app = Flask(__name__)
app.config["SECRET_KEY"] = "thessboard-secret"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

ROOMS: dict[str, dict[str, Any]] = {}
SID_TO_ROOM: dict[str, str] = {}
SID_TO_PLAYER: dict[str, str] = {}

PLAYER_COLORS = ["#c74444", "#2e6ee8", "#1f9d62"]
START_NODES = [0, 13, 38]
AUTO_END_DELAY = 1.0
BOT_DELAY = 2.2


def random_room_code(length: int = 5) -> str:
    while True:
        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=length))
        if code not in ROOMS:
            return code


def shuffle_copy(items: list[dict[str, Any]]) -> deque:
    copied = list(items)
    random.shuffle(copied)
    return deque(copied)


def fresh_room_state(room_code: str) -> dict[str, Any]:
    return {
        "roomCode": room_code,
        "hostSid": None,
        "started": False,
        "winnerId": None,
        "players": [],
        "turnIndex": 0,
        "lastDice": None,
        "phase": "lobby",
        "pendingCard": None,
        "pendingChoices": None,
        "maxPlayers": 3,
        "decks": {
            "eventCards": shuffle_copy(GAME_DATA["eventCards"]),
            "eventDiscard": [],
            "metroQuestions": shuffle_copy(GAME_DATA["metroQuestions"]),
            "metroDiscard": [],
            "monument": {name: shuffle_copy(deck["cards"]) for name, deck in GAME_DATA["decks"].items()},
            "monumentDiscard": {name: [] for name in GAME_DATA["decks"]},
        },
        "lastMove": None,
        "log": ["Δημιουργήθηκε νέο δωμάτιο."],
        "scheduledAutoEnd": False,
        "scheduledBotTurn": False,
        "mode": "multiplayer",
        "controllerSid": None,
    }


def public_state(room: dict[str, Any]) -> dict[str, Any]:
    return {
        "roomCode": room["roomCode"],
        "started": room["started"],
        "winnerId": room["winnerId"],
        "players": [{k: v for k, v in p.items() if k != "sid"} for p in room["players"]],
        "turnIndex": room["turnIndex"],
        "lastDice": room["lastDice"],
        "phase": room["phase"],
        "pendingCard": room["pendingCard"],
        "pendingChoices": room["pendingChoices"],
        "cardsToWin": GAME_DATA["cardsToWin"],
        "log": room["log"][:18],
        "title": GAME_DATA["title"],
        "lastMove": room["lastMove"],
        "mode": room.get("mode", "multiplayer"),
    }


def node_by_id(node_id: int) -> dict[str, Any]:
    for node in GAME_DATA["nodes"]:
        if node["id"] == node_id:
            return node
    raise KeyError(node_id)


def previous_node_id(node_id: int) -> int:
    for node in GAME_DATA["nodes"]:
        if node["next"] == node_id:
            return node["id"]
    return node_id


def current_player(room: dict[str, Any]) -> dict[str, Any] | None:
    if not room["players"]:
        return None
    return room["players"][room["turnIndex"]]


def get_room_by_sid(sid: str) -> dict[str, Any] | None:
    room_code = SID_TO_ROOM.get(sid)
    return ROOMS.get(room_code) if room_code else None


def get_player(room: dict[str, Any], sid: str) -> dict[str, Any] | None:
    player_id = SID_TO_PLAYER.get(sid)
    for player in room["players"]:
        if player["id"] == player_id:
            return player
    return None


def actor_is_allowed(room: dict[str, Any], sid: str) -> bool:
    current = current_player(room)
    if not current:
        return False
    if room.get("controllerSid") == sid:
        return True
    return current.get("sid") == sid


def add_log(room: dict[str, Any], text: str) -> None:
    room["log"].insert(0, text)
    room["log"] = room["log"][:30]


def emit_state(room: dict[str, Any]) -> None:
    socketio.emit("state_update", public_state(room), room=room["roomCode"])


def draw_simple_card(room: dict[str, Any], deck_key: str, discard_key: str) -> dict[str, Any]:
    deck = room["decks"][deck_key]
    discard = room["decks"][discard_key]
    if not deck:
        random.shuffle(discard)
        room["decks"][deck_key] = deck = deque(discard)
        room["decks"][discard_key] = []
    card = deck.popleft()
    room["decks"][discard_key].append(card)
    return card


def draw_monument_card(room: dict[str, Any], deck_name: str) -> dict[str, Any]:
    deck = room["decks"]["monument"][deck_name]
    discard = room["decks"]["monumentDiscard"][deck_name]
    if not deck:
        random.shuffle(discard)
        room["decks"]["monument"][deck_name] = deck = deque(discard)
        room["decks"]["monumentDiscard"][deck_name] = []
    card = deck.popleft()
    room["decks"]["monumentDiscard"][deck_name].append(card)
    return card


def set_last_move(room: dict[str, Any], player: dict[str, Any], path: list[int], reason: str) -> None:
    room["lastMove"] = {"playerId": player["id"], "path": path, "reason": reason}


def follow_steps(start_node_id: int, steps: int) -> list[int]:
    current = start_node_id
    path = []
    for _ in range(abs(steps)):
        current = node_by_id(current)["next"] if steps >= 0 else previous_node_id(current)
        path.append(current)
    return path


def move_player_along_path(player: dict[str, Any], path: list[int]) -> None:
    if path:
        player["nodeId"] = path[-1]


def grant_monument_card(player: dict[str, Any]) -> None:
    player["monumentCards"] += 1


def check_victory(room: dict[str, Any], player: dict[str, Any]) -> None:
    if player["monumentCards"] >= GAME_DATA["cardsToWin"]:
        room["winnerId"] = player["id"]
        room["phase"] = "finished"
        add_log(room, f"🏆 Νίκησε ο/η {player['name']} με {player['monumentCards']} κάρτες μνημείων!")


def build_landmark_choices() -> list[dict[str, Any]]:
    seen = set()
    choices = []
    for node in GAME_DATA["nodes"]:
        if node["kind"] != "landmark":
            continue
        if node["label"] in seen:
            continue
        seen.add(node["label"])
        choices.append({"id": node["id"], "label": node["label"], "deck": node.get("deck")})
    return choices


def rotate_turn(room: dict[str, Any]) -> None:
    if room["winnerId"]:
        return
    room["turnIndex"] = (room["turnIndex"] + 1) % len(room["players"])
    room["lastDice"] = None
    room["phase"] = "await_roll"
    room["pendingCard"] = None
    room["pendingChoices"] = None
    room["lastMove"] = None
    add_log(room, f"Σειρά του/της {current_player(room)['name']}.")


def queue_auto_end(room: dict[str, Any]) -> None:
    if room["winnerId"] or room["phase"] != "await_end_turn" or room.get("scheduledAutoEnd"):
        return
    room["scheduledAutoEnd"] = True
    room_code = room["roomCode"]

    def _task():
        socketio.sleep(AUTO_END_DELAY)
        room2 = ROOMS.get(room_code)
        if not room2:
            return
        room2["scheduledAutoEnd"] = False
        if room2["winnerId"] or room2["phase"] != "await_end_turn":
            return
        rotate_turn(room2)
        emit_state(room2)
        queue_bot_turn_if_needed(room2)

    socketio.start_background_task(_task)


def queue_bot_turn_if_needed(room: dict[str, Any]) -> None:
    player = current_player(room)
    if not player or not player.get("isBot") or room["winnerId"] or room.get("scheduledBotTurn"):
        return
    if room["phase"] not in {"await_roll", "await_card_resolution", "await_answer", "await_landmark_choice"}:
        return
    room["scheduledBotTurn"] = True
    room_code = room["roomCode"]

    def _bot_task():
        socketio.sleep(BOT_DELAY)
        room2 = ROOMS.get(room_code)
        if not room2:
            return
        room2["scheduledBotTurn"] = False
        bot_take_action(room2)

    socketio.start_background_task(_bot_task)


def resolve_landing(room: dict[str, Any], player: dict[str, Any]) -> None:
    node = node_by_id(player["nodeId"])
    if node["kind"] == "event":
        room["pendingCard"] = {"type": "event", "card": draw_simple_card(room, "eventCards", "eventDiscard"), "node": node}
        room["phase"] = "await_card_resolution"
        return
    if node["kind"] == "metro":
        room["pendingCard"] = {"type": "metro", "card": draw_simple_card(room, "metroQuestions", "metroDiscard"), "node": node}
        room["phase"] = "await_answer"
        return
    if node["kind"] == "landmark":
        room["pendingCard"] = {"type": "monument", "card": draw_monument_card(room, node["deck"]), "node": node}
        room["phase"] = "await_answer"
        return
    room["phase"] = "await_end_turn"


def maybe_finish_turn(room: dict[str, Any]) -> None:
    if room["winnerId"]:
        emit_state(room)
        return
    emit_state(room)
    if room["phase"] == "await_end_turn":
        queue_auto_end(room)
    else:
        queue_bot_turn_if_needed(room)


def apply_event_card(room: dict[str, Any], player: dict[str, Any], text: str) -> None:
    lower = text.lower()
    if "αφετηρία" in lower:
        player["nodeId"] = player["startNode"]
        set_last_move(room, player, [player["startNode"]], "event_to_start")
    elif "6 κουτάκια πίσω" in lower:
        path = follow_steps(player["nodeId"], -6); move_player_along_path(player, path); set_last_move(room, player, path, "event_back")
    elif "3 κουτάκια πίσω" in lower:
        path = follow_steps(player["nodeId"], -3); move_player_along_path(player, path); set_last_move(room, player, path, "event_back")
    elif "2 κουτάκια πίσω" in lower:
        path = follow_steps(player["nodeId"], -2); move_player_along_path(player, path); set_last_move(room, player, path, "event_back")
    elif "3 κουτάκια μπροστά" in lower:
        path = follow_steps(player["nodeId"], 3); move_player_along_path(player, path); set_last_move(room, player, path, "event_forward")
    elif "4 κουτακια" in lower or "4 κουτάκια" in lower:
        path = follow_steps(player["nodeId"], 4); move_player_along_path(player, path); set_last_move(room, player, path, "event_forward")
    elif "6 κουτάκια μπροστά" in lower:
        path = follow_steps(player["nodeId"], 6); move_player_along_path(player, path); set_last_move(room, player, path, "event_forward")
    elif "πάρε μία κάρτα μνημείου από τη στοίβα" in lower:
        grant_monument_card(player)
    elif "πάρε μία κάρτα μνημείου από έναν συμπαίκτη" in lower:
        others = [p for p in room["players"] if p["id"] != player["id"] and p["monumentCards"] > 0]
        if others:
            donor = sorted(others, key=lambda p: p["monumentCards"], reverse=True)[0]
            donor["monumentCards"] -= 1
            player["monumentCards"] += 1
    elif "χάρισε" in lower and "κάρτα μνημείου" in lower:
        others = [p for p in room["players"] if p["id"] != player["id"]]
        if others and player["monumentCards"] > 0:
            player["monumentCards"] -= 1
            others[0]["monumentCards"] += 1
    elif "επιπλέον προσπάθεια" in lower:
        player["bonusMonumentTry"] = True
    elif "αντάλλαξε θέση" in lower:
        others = [p for p in room["players"] if p["id"] != player["id"]]
        if others:
            leader = sorted(others, key=lambda p: p["monumentCards"], reverse=True)[0]
            player["nodeId"], leader["nodeId"] = leader["nodeId"], player["nodeId"]
            room["lastMove"] = {"swap": [player["id"], leader["id"]]}
    elif "ρίξε ξανά" in lower:
        room["phase"] = "await_roll"
        room["lastDice"] = None
        room["pendingCard"] = None
        add_log(room, f"{player['name']} κερδίζει νέο ρίξιμο.")
        return
    elif "μνημείο της επιλογής" in lower or "ξενάγηση" in lower:
        room["pendingChoices"] = build_landmark_choices()
        room["phase"] = "await_landmark_choice"
        room["pendingCard"] = None
        return

    room["pendingCard"] = None
    check_victory(room, player)
    if room["winnerId"]:
        return
    resolve_landing(room, player)
    if room["phase"] not in {"await_answer", "await_card_resolution", "await_landmark_choice"}:
        room["phase"] = "await_end_turn"


def bot_take_action(room: dict[str, Any]) -> None:
    if room["winnerId"]:
        return
    player = current_player(room)
    if not player or not player.get("isBot"):
        return

    if room["phase"] == "await_roll":
        dice_value = random.randint(1, 6)
        room["lastDice"] = dice_value
        path = follow_steps(player["nodeId"], dice_value)
        move_player_along_path(player, path)
        set_last_move(room, player, path, "dice_roll")
        landed_node = node_by_id(player["nodeId"])
        destination = landed_node["label"] or landed_node["kind"]
        add_log(room, f"{player['name']} έφερε {dice_value} και έφτασε στο «{destination}».")
        resolve_landing(room, player)
        maybe_finish_turn(room)
        return

    if room["phase"] == "await_card_resolution":
        text = room["pendingCard"]["card"]["text"]
        add_log(room, f"{player['name']} ενεργοποίησε συμβάν: {text}")
        apply_event_card(room, player, text)
        maybe_finish_turn(room)
        return

    if room["phase"] == "await_answer":
        pending = room["pendingCard"]
        options = pending["card"].get("options") or []
        correct_answer = pending["card"].get("answer", "")
        answer = correct_answer if random.random() < 0.7 else random.choice(options or [correct_answer])
        correct = answer.strip().lower() == correct_answer.strip().lower()
        if correct:
            if pending["type"] == "metro":
                add_log(room, f"{player['name']} απάντησε σωστά σε κάρτα μετρό.")
                room["pendingChoices"] = build_landmark_choices()
                room["pendingCard"] = None
                room["phase"] = "await_landmark_choice"
            else:
                grant_monument_card(player)
                add_log(room, f"{player['name']} κέρδισε κάρτα μνημείου από το «{pending['node']['label']}».")
                room["pendingCard"] = None
                check_victory(room, player)
                if not room["winnerId"]:
                    room["phase"] = "await_end_turn"
        else:
            add_log(room, f"{player['name']} απάντησε λάθος.")
            room["pendingCard"] = None
            room["phase"] = "await_end_turn"
        maybe_finish_turn(room)
        return

    if room["phase"] == "await_landmark_choice":
        choices = room.get("pendingChoices") or []
        if not choices:
            room["phase"] = "await_end_turn"
            maybe_finish_turn(room)
            return
        choice = random.choice(choices)
        player["nodeId"] = int(choice["id"])
        room["pendingChoices"] = None
        room["lastMove"] = {"playerId": player["id"], "path": [player["nodeId"]], "reason": "metro_jump"}
        add_log(room, f"{player['name']} μεταφέρθηκε στο «{node_by_id(player['nodeId'])['label']}».")
        resolve_landing(room, player)
        maybe_finish_turn(room)


def build_player(idx: int, name: str, sid: str | None, is_bot: bool = False) -> dict[str, Any]:
    return {
        "id": f"p{idx + 1}",
        "name": name[:18] or (f"Bot {idx}" if is_bot else f"Παίκτης {idx+1}"),
        "color": PLAYER_COLORS[idx],
        "startNode": START_NODES[idx],
        "nodeId": START_NODES[idx],
        "monumentCards": 0,
        "bonusMonumentTry": False,
        "connected": True,
        "sid": sid,
        "isBot": is_bot,
    }


def start_room(room: dict[str, Any]) -> None:
    room["started"] = True
    room["phase"] = "await_roll"
    add_log(room, "Το παιχνίδι ξεκίνησε.")
    add_log(room, f"Πρώτος παίζει ο/η {current_player(room)['name']}.")
    emit_state(room)
    queue_bot_turn_if_needed(room)


@app.get("/")
def index():
    return render_template("index.html", title=GAME_DATA["title"])


@app.get("/privacy")
def privacy():
    return render_template("privacy.html", title=f"{GAME_DATA['title']} · Privacy")


@app.get("/credits")
def credits():
    return render_template("credits.html", title=f"{GAME_DATA['title']} · Credits")


@socketio.on("create_room")
def on_create_room(data: dict[str, Any]):
    name = (data.get("name") or "Host").strip()[:18] or "Host"
    room_code = random_room_code()
    room = fresh_room_state(room_code)
    room["hostSid"] = request.sid
    room["mode"] = "multiplayer"
    player = build_player(0, name, request.sid, False)
    room["players"].append(player)
    ROOMS[room_code] = room
    SID_TO_ROOM[request.sid] = room_code
    SID_TO_PLAYER[request.sid] = player["id"]
    join_room(room_code)
    add_log(room, f"Ο/Η {name} δημιούργησε το δωμάτιο {room_code}.")
    emit("room_created", {"roomCode": room_code, "playerId": player["id"]})
    emit_state(room)


@socketio.on("create_hotseat")
def on_create_hotseat(data: dict[str, Any]):
    names = data.get("names") or []
    names = [str(n).strip()[:18] for n in names if str(n).strip()][:3]
    if len(names) < 2:
        emit("server_error", {"message": "Βάλε τουλάχιστον 2 ονόματα για hotseat."})
        return
    room_code = random_room_code()
    room = fresh_room_state(room_code)
    room["hostSid"] = request.sid
    room["controllerSid"] = request.sid
    room["mode"] = "hotseat"
    for idx, name in enumerate(names):
        room["players"].append(build_player(idx, name, request.sid, False))
    ROOMS[room_code] = room
    SID_TO_ROOM[request.sid] = room_code
    SID_TO_PLAYER[request.sid] = room["players"][0]["id"]
    join_room(room_code)
    add_log(room, f"Ξεκίνησε hotseat match για {', '.join(names)}.")
    emit("room_created", {"roomCode": room_code, "playerId": room["players"][0]["id"]})
    start_room(room)


@socketio.on("create_bot_match")
def on_create_bot_match(data: dict[str, Any]):
    name = (data.get("name") or "Παίκτης").strip()[:18] or "Παίκτης"
    bot_count = max(1, min(2, int(data.get("botCount") or 1)))
    room_code = random_room_code()
    room = fresh_room_state(room_code)
    room["hostSid"] = request.sid
    room["controllerSid"] = request.sid
    room["mode"] = "bots"
    room["players"].append(build_player(0, name, request.sid, False))
    for idx in range(bot_count):
        room["players"].append(build_player(idx + 1, f"Bot {idx + 1}", None, True))
    ROOMS[room_code] = room
    SID_TO_ROOM[request.sid] = room_code
    SID_TO_PLAYER[request.sid] = room["players"][0]["id"]
    join_room(room_code)
    add_log(room, f"Ξεκίνησε match με {bot_count} bot.")
    emit("room_created", {"roomCode": room_code, "playerId": room["players"][0]["id"]})
    start_room(room)


@socketio.on("join_room_request")
def on_join_room(data: dict[str, Any]):
    room_code = (data.get("roomCode") or "").strip().upper()
    name = (data.get("name") or "Παίκτης").strip()[:18] or "Παίκτης"
    room = ROOMS.get(room_code)
    if not room:
        emit("server_error", {"message": "Το δωμάτιο δεν βρέθηκε."})
        return
    if room["started"] or len(room["players"]) >= room["maxPlayers"]:
        emit("server_error", {"message": "Το δωμάτιο είναι γεμάτο ή έχει ξεκινήσει."})
        return
    idx = len(room["players"])
    player = {"id": f"p{idx + 1}", "name": name, "color": PLAYER_COLORS[idx], "startNode": START_NODES[idx], "nodeId": START_NODES[idx], "monumentCards": 0, "bonusMonumentTry": False, "connected": True, "sid": request.sid, "isBot": False}
    room["players"].append(player)
    SID_TO_ROOM[request.sid] = room_code
    SID_TO_PLAYER[request.sid] = player["id"]
    join_room(room_code)
    add_log(room, f"Ο/Η {name} μπήκε στο δωμάτιο.")
    emit("room_joined", {"roomCode": room_code, "playerId": player["id"]})
    emit_state(room)


@socketio.on("add_bot")
def on_add_bot():
    room = get_room_by_sid(request.sid)
    if not room:
        return
    if request.sid != room["hostSid"]:
        emit("server_error", {"message": "Μόνο ο host μπορεί να προσθέσει bot."})
        return
    if room["started"] or len(room["players"]) >= room["maxPlayers"]:
        emit("server_error", {"message": "Δεν υπάρχει διαθέσιμη θέση για bot."})
        return
    idx = len(room["players"])
    player = build_player(idx, f"Bot {idx}", None, True)
    room["players"].append(player)
    add_log(room, f"Προστέθηκε ο/η {player['name']}.")
    emit_state(room)


@socketio.on("start_game")
def on_start_game():
    room = get_room_by_sid(request.sid)
    if not room:
        return
    if request.sid != room["hostSid"]:
        emit("server_error", {"message": "Μόνο ο host μπορεί να ξεκινήσει."})
        return
    if len(room["players"]) < 2:
        emit("server_error", {"message": "Χρειάζονται τουλάχιστον 2 παίκτες ή bots."})
        return
    start_room(room)


@socketio.on("roll_dice")
def on_roll_dice():
    room = get_room_by_sid(request.sid)
    if not room or room["phase"] != "await_roll" or room["winnerId"]:
        return
    player = current_player(room) if room.get("controllerSid") == request.sid else get_player(room, request.sid)
    if not player or not actor_is_allowed(room, request.sid):
        return
    dice_value = random.randint(1, 6)
    room["lastDice"] = dice_value
    path = follow_steps(player["nodeId"], dice_value)
    move_player_along_path(player, path)
    set_last_move(room, player, path, "dice_roll")
    landed_node = node_by_id(player["nodeId"])
    destination = landed_node["label"] or landed_node["kind"]
    add_log(room, f"{player['name']} έφερε {dice_value} και έφτασε στο «{destination}».")
    resolve_landing(room, player)
    maybe_finish_turn(room)


@socketio.on("resolve_event_card")
def on_resolve_event_card():
    room = get_room_by_sid(request.sid)
    if not room or room["phase"] != "await_card_resolution":
        return
    player = current_player(room) if room.get("controllerSid") == request.sid else get_player(room, request.sid)
    if not player or not actor_is_allowed(room, request.sid):
        return
    text = room["pendingCard"]["card"]["text"]
    add_log(room, f"Συμβάν: {text}")
    apply_event_card(room, player, text)
    maybe_finish_turn(room)


@socketio.on("submit_answer")
def on_submit_answer(data: dict[str, Any]):
    room = get_room_by_sid(request.sid)
    if not room or room["phase"] != "await_answer":
        return
    player = current_player(room) if room.get("controllerSid") == request.sid else get_player(room, request.sid)
    if not player or not actor_is_allowed(room, request.sid):
        return
    pending = room["pendingCard"]
    selected = (data.get("answer") or "").strip()
    correct = selected.lower() == pending["card"]["answer"].strip().lower()
    if correct:
        if pending["type"] == "metro":
            add_log(room, f"{player['name']} απάντησε σωστά σε κάρτα μετρό.")
            room["pendingChoices"] = build_landmark_choices()
            room["pendingCard"] = None
            room["phase"] = "await_landmark_choice"
        else:
            grant_monument_card(player)
            add_log(room, f"{player['name']} κέρδισε κάρτα μνημείου από το «{pending['node']['label']}».")
            room["pendingCard"] = None
            check_victory(room, player)
            if not room["winnerId"]:
                room["phase"] = "await_end_turn"
    else:
        add_log(room, f"{player['name']} απάντησε λάθος.")
        if pending["type"] == "monument" and player.get("bonusMonumentTry"):
            player["bonusMonumentTry"] = False
            add_log(room, f"Ο/Η {player['name']} έχει bonus δεύτερη προσπάθεια.")
        else:
            room["pendingCard"] = None
            room["phase"] = "await_end_turn"
    maybe_finish_turn(room)


@socketio.on("choose_landmark")
def on_choose_landmark(data: dict[str, Any]):
    room = get_room_by_sid(request.sid)
    if not room or room["phase"] != "await_landmark_choice":
        return
    player = current_player(room) if room.get("controllerSid") == request.sid else get_player(room, request.sid)
    if not player or not actor_is_allowed(room, request.sid):
        return
    node_id = int(data.get("nodeId"))
    player["nodeId"] = node_id
    room["pendingChoices"] = None
    room["lastMove"] = {"playerId": player["id"], "path": [node_id], "reason": "metro_jump"}
    add_log(room, f"{player['name']} μεταφέρθηκε στο «{node_by_id(node_id)['label']}».")
    resolve_landing(room, player)
    maybe_finish_turn(room)


@socketio.on("disconnect")
def on_disconnect():
    room = get_room_by_sid(request.sid)
    if not room:
        return
    player = get_player(room, request.sid)
    if player:
        player["connected"] = False
        add_log(room, f"Ο/Η {player['name']} αποσυνδέθηκε.")
    SID_TO_ROOM.pop(request.sid, None)
    SID_TO_PLAYER.pop(request.sid, None)
    emit_state(room)


if __name__ == "__main__":
    socketio.run(app, debug=True, host="0.0.0.0", port=5000)
