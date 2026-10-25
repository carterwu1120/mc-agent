# Setup and troubleshooting

## Requirements

- Node.js 22 or newer (`node --version`).
- A Minecraft **Java Edition** server the bot can reach. Bedrock is not supported.
  Tested with vanilla 1.20.1. `--version auto` lets mineflayer detect the version.
- `npm` access to the public registry for the one-time `mcctl setup`
  (mineflayer, mineflayer-pathfinder, ws, minecraft-protocol-forge).

## Server options

| setup | how |
|---|---|
| Single-player world | Esc → Open to LAN. Use the port the game prints, not 25565. Enable "Allow Cheats" if you want `tp`/`home`/`back`. The bot disconnects when you close the world. |
| Dedicated server | `online-mode=false` in `server.properties` for a test server with `--auth offline`. For an online-mode server use `--auth microsoft` with an account that owns Java Edition. |
| Operator rights | `op <botname>` in the server console makes `tp`, `home`, `back` work |

## Options for `mcctl start`

| flag | default | notes |
|---|---|---|
| `--host` | `localhost` | |
| `--port` | `25565` | |
| `--username` | `Agent` | in-game name (offline) or account e-mail (microsoft) |
| `--version` | `auto` | set e.g. `1.20.1` if auto-detection fails |
| `--auth` | `offline` | `microsoft`: sign-in URL/code is printed on stderr. The token cache lives in the mineflayer profile folder. |
| `--forge` | off | Forge handshake for modded servers. Mod compatibility is not guaranteed. |
| `--chat` | `addressed` | `all` delivers every player message as a `chat` event |
| `--name` | `default` | separate bot instance; state in `~/.mcbot/<name>/` |
| `--timeout` | `90` | seconds to wait for the bot to enter the world |

Environment variables `MC_HOST`, `MC_PORT`, `MC_USERNAME`, `MC_AUTH`, `MC_VERSION`
are used when the flags are absent. `MCBOT_HOME` moves the state directory.
`MC_ADAPTIVE_NAVIGATION=true` enables the experimental mining navigator.

## What runs where

`mcctl start` launches a background daemon that starts `scripts/bot/index.js`.
The daemon talks to the bot over a WebSocket and serves the CLI over HTTP. Both
listen on random ports bound to `127.0.0.1` only. Files in `~/.mcbot/<name>/`:

- `config.json`: last start options
- `daemon.json`: pids and ports while running
- `bot.log`: bot console output (`mcctl log`)
- `data/`: home position, chests, mode (persist across restarts)
- `logs/`: structured JSONL logs, old files are pruned automatically

The bot's own in-game chat replies are in Traditional Chinese.

## Troubleshooting

| symptom | fix |
|---|---|
| `dependencies are not installed` | `mcctl setup` |
| `started: false`, log shows `ECONNREFUSED` | wrong host/port, or the server/LAN world is not open |
| log shows a version or protocol error | pass `--version <server version>` |
| kicked: `You are not white-listed` / `Failed to verify username` | whitelist the bot, or the server is online-mode: use `--auth microsoft` |
| `tp`/`home` do nothing | the bot is not an operator / cheats are off |
| `mc_disconnected` event | the server kicked the bot or closed. `mcctl log`, then `mcctl stop` + `mcctl start` |
| `bot is not connected` on `run` | the bot is still joining or has exited. Run `mcctl status` |
