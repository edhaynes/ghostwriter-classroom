# Ghostwriter Classroom

AI-powered collaborative storytelling classroom demo featuring real-time grading, peer voting, and human vs. AI comparison.

## Features

- 📖 **Story Arc Setup** - Instructor defines 3-step story structure
- ⚖️ **Custom Rubric** - Define grading criteria
- ⏱️ **Timed Writing** - 5-minute story creation sprint (configurable)
- 🤖 **AI Grading** - Automatic story evaluation via Groq LLM
- 👥 **Peer Voting** - Students vote on favorites
- 🏆 **Results Reveal** - Winner announcement with full leaderboard
- 🎭 **Demo Mode** - Auto-simulate entire classroom (2-10 students)
- 💾 **Redis Persistence** - Sessions survive server restarts
- ⏰ **Idle Shutdown** - Auto-shutdown after 1 hour to save resources
- 🎩 **Red Hat Branding** - Logo and styling

## Quick Start

### Local Development

```bash
# Start Redis
podman run -d --name classroom-redis -p 6379:6379 redis:7-alpine

# Set environment
export GROQ_API_KEY="your-api-key"

# Run server
uvicorn main:app --reload --port 8081
```

Visit: http://localhost:8081

### OpenShift Deployment

```bash
# Login
oc login <cluster-url>

# Configure secrets
nano openshift/secret.yaml  # Add your Groq API key

# Deploy
./deploy-openshift.sh
```

See [OPENSHIFT_DEPLOYMENT.md](OPENSHIFT_DEPLOYMENT.md) for full details.

## Resource Management

**Idle in cloud?** The app automatically shuts down after 1 hour of inactivity.

```bash
# Manual control
cd openshift
./scale-down.sh  # Free resources
./scale-up.sh    # Activate again
```

See [IDLE_SHUTDOWN.md](IDLE_SHUTDOWN.md) for details.

## Architecture

- **Frontend**: Single-page app (HTML/CSS/JavaScript)
- **Backend**: FastAPI (Python)
- **Storage**: Redis (sessions)
- **AI**: Groq API (story generation + grading)
- **Container**: Red Hat UBI9 Python

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379` | Redis connection |
| `GROQ_API_KEY` | - | Required for AI features |
| `CLASSROOM_MODEL` | `llama-3.3-70b-versatile` | Groq model name |
| `ENABLE_IDLE_SHUTDOWN` | `false` (local), `true` (cloud) | Auto-shutdown |
| `IDLE_TIMEOUT_SECONDS` | `3600` | Idle time before shutdown |

## Files

```
ghostwriter_classroom/
├── main.py                     # FastAPI entry point
├── classroom/
│   ├── api.py                  # REST endpoints
│   ├── session_manager.py      # Session state + Redis
│   ├── grader.py               # AI grading
│   ├── models.py               # Pydantic models
│   ├── username_pool.py        # Random name generation
│   └── idle_monitor.py         # Auto-shutdown logic
├── static/
│   └── index.html              # Frontend SPA
├── openshift/                  # Deployment configs
├── requirements.txt
├── Containerfile               # UBI9 container
└── docker-compose.yml          # Local Redis
```

## Workflow

1. **SETUP**: Instructor configures arc + rubric
2. **WRITING**: Students write stories (5 min timer, configurable)
3. **REVIEW**: AI grading + peer voting
4. **FINAL_VOTE**: Vote on top 2 finalists
5. **REVEAL**: Winner announced with scores

All phases auto-advance when complete!

## Demo Mode

Perfect for testing or live demos:

1. Open instructor URL
2. Scroll to "Demo Mode"
3. Set student count (2-10)
4. Click "Run Full Simulation"
5. Watch full workflow in ~60 seconds

Stories use 8 different archetypes for variety.

## License

MIT

## Support

Issues: https://github.com/anthropics/claude-code/issues
