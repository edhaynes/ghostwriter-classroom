# Ghostwriter Classroom

AI-powered collaborative classroom demo featuring real-time grading and human vs. AI comparison. Supports two modes:
- **Storytelling**: Students craft stories with peer voting
- **Intrusion Detection**: Students write network detection logic for cybersecurity training

## Session Modes

### Storytelling Mode
- 📖 **Story Arc Setup** - Instructor defines 3-step story structure
- ⏱️ **Timed Writing** - 5-minute story creation sprint (configurable)
- 👥 **Peer Voting** - Students vote on favorites
- 🏆 **Winner Selection** - Combined AI + peer score determines finalists

### Intrusion Detection Mode
- 🌐 **Network Traffic Generation** - Synthetic attack scenarios (port scans, IP spoofing)
- 🔍 **Detection Logic** - Students write scripts to identify intrusions
- 🤖 **AI Grading Only** - No peer voting; automatic evaluation
- 📊 **Traffic Viewer** - Inspect attack data during reveal

📘 **See [INTRUSION_DETECTION_DEMO.md](INTRUSION_DETECTION_DEMO.md) for instructor guide**

## Common Features

- ⚖️ **Custom Rubric** - Define grading criteria
- 🤖 **AI Grading** - Automatic evaluation via Groq/Ollama/KServe
- 🎭 **Demo Mode** - Auto-simulate entire classroom (2-40 students)
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

### Storytelling Mode
1. **SETUP**: Instructor configures story arc + rubric
2. **WRITING**: Students write stories (5 min timer, configurable)
3. **REVIEW**: AI grading + peer voting
4. **FINAL_VOTE**: Vote on top 2 finalists
5. **REVEAL**: Winner announced with scores

### Intrusion Detection Mode
1. **SETUP**: Instructor generates network traffic + configures rubric
2. **WRITING**: Students write detection logic (5 min timer, configurable)
3. **REVIEW**: AI grading only (no peer voting)
4. **REVEAL**: Winner announced with scores, view traffic data

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
