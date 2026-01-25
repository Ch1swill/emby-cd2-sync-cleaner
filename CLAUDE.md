# CLAUDE.md
用中文同我交流
This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Emby-CD2-Sync-Cleaner is a Docker-based webhook service that automatically deletes source video files from CloudDrive2-mounted network storage (115网盘) when users delete `.strm` files in Emby. It bridges the gap between Emby's deletion of small `.strm` index files and the actual GB-sized source videos on cloud storage.

## Architecture

```
Emby (delete media) → POST /webhook → app.py → Path mapping → File search → Delete → CD2 syncs to cloud
```

**Core flow in app.py:**
1. Webhook handler validates event type (`library.deleted` or `item.deleted`)
2. Safety checks: validates `.strm` extension, minimum filename length
3. Path mapping: converts Emby path to container mount path using `config.json`
4. Smart navigation: falls back to root search if precise directory missing
5. File deletion: matches by base name, supports variant extensions
6. Directory cleanup: per-mapping configurable via `clean_dirs`

## Build and Run Commands

```bash
# Build Docker image
docker build -t emby-cd2-sync-cleaner .

# Run with Docker Compose
docker-compose up -d

# View logs
docker logs -f emby-cd2-sync-cleaner
```

## Configuration

**config.json structure:**
```json
{
  "min_filename_length": 4,
  "path_mapping": {
    "EMBY_PATH": {
      "local_path": "CONTAINER_MOUNT_PATH",
      "clean_dirs": true
    }
  }
}
```

Path mappings are sorted by longest match first. Each mapping supports:
- Object format: `{"local_path": "...", "clean_dirs": true/false}`
- String format: `"/path"` (defaults to `clean_dirs: true`)

**Three-way alignment principle:** Host path, docker-compose volume mapping, and config `local_path` must be consistent.

## Key Files

| File | Purpose |
|------|---------|
| `app.py` | Flask webhook handler with path mapping and deletion logic |
| `config.json` | Emby-to-container path mappings with per-directory cleanup settings |
| `Dockerfile` | Python 3.9-slim base with Flask/Waitress |
| `docker-compose.yaml` | Example deployment configuration |

## Dependencies

- `flask` - Web framework
- `waitress` - Production WSGI server (port 5005)

## CI/CD

GitHub Actions workflow (`.github/workflows/publish.yml`) automatically builds and pushes to Docker Hub (`ch1swill/emby-cd2-sync-cleaner:latest`) on pushes to main and version tags.

## Safety Mechanisms

- Only processes `.strm` files (case-insensitive)
- Minimum filename length validation (default 4 chars)
- Operations contained within configured mount paths
- Non-recursive filename matching with variant extensions
