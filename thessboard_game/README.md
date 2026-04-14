# Thessboard Game

A mobile-first Flask + Socket.IO board game with three modes:
- Same device / hotseat
- Online multiplayer
- Play with bots

## Local run
```bash
python -m venv .venv
. .venv/bin/activate  # or Windows activate script
pip install -r requirements.txt
python app.py
```

## Production starter
This project now includes:
- `Procfile`
- `gunicorn` in requirements
- `privacy` page
- `credits` page
- room-based Socket.IO flow

Suggested production steps:
1. Set `debug=False`
2. Run behind HTTPS
3. Use Redis + a message queue if you want to scale Socket.IO
4. Replace placeholder assets with fully licensed media
5. Add a final privacy policy matching your hosting/analytics setup

## Notes
- Room state is currently kept in memory.
- For a bigger public release, add persistent storage and reconnection recovery.
