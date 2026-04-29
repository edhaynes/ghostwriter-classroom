# Idle Shutdown Feature

## Overview

The Ghostwriter Classroom includes an **automatic idle shutdown** feature to save cloud resources when the demo is not actively in use.

**Default behavior:**
- ✅ Enabled in OpenShift deployment
- ⏱️ Shuts down after **1 hour** of inactivity
- 🔄 Automatically restarts when needed (Kubernetes behavior)
- 💰 Saves resources and costs

## How It Works

1. **Activity Tracking**: Every HTTP request (except `/health`) resets the idle timer
2. **Background Monitor**: Checks idle time every 60 seconds
3. **Graceful Shutdown**: After timeout, pod exits cleanly
4. **Auto-Restart**: Kubernetes restarts the pod when new requests arrive

### What Counts as "Activity"?

- Creating/joining sessions
- Submitting stories
- Viewing pages (any page load)
- Running simulations
- WebSocket connections

### What Doesn't Count?

- Health check probes (from Kubernetes)
- Background tasks (Redis, session cleanup)

## Configuration

### Environment Variables

Set in `openshift/classroom-deployment.yaml`:

```yaml
- name: ENABLE_IDLE_SHUTDOWN
  value: "true"  # Set to "false" to disable

- name: IDLE_TIMEOUT_SECONDS
  value: "3600"  # 1 hour (adjust as needed)
```

### Common Timeouts

| Duration | Seconds | Use Case |
|----------|---------|----------|
| 15 min   | 900     | Short demos |
| 30 min   | 1800    | Typical classroom |
| 1 hour   | 3600    | **Default** |
| 2 hours  | 7200    | Long workshops |
| Disabled | -       | Set ENABLE_IDLE_SHUTDOWN=false |

## Checking Idle Status

### API Endpoint

```bash
curl https://<your-route>/idle-status
```

Response:
```json
{
  "idle_shutdown_enabled": true,
  "idle_seconds": 234,
  "timeout_seconds": 3600,
  "remaining_seconds": 3366,
  "will_shutdown_in": "56 minutes"
}
```

### CLI Check

```bash
oc logs -f deployment/ghostwriter-classroom -n ghostwriter-classroom | grep -i idle
```

You'll see:
```
INFO:classroom.idle_monitor:Idle monitor started: shutdown after 3600s of inactivity
INFO:classroom.idle_monitor:Approaching idle timeout: 2880s / 3600s
WARNING:classroom.idle_monitor:Idle for 3600s, triggering shutdown...
```

## Manual Resource Management

### Quick Commands

**Scale down (free resources):**
```bash
cd openshift
./scale-down.sh
```

**Scale up (activate):**
```bash
cd openshift
./scale-up.sh
```

**Check status:**
```bash
oc get pods -n ghostwriter-classroom
```

### Full CLI Control

```bash
# Scale down to 0 (stops all pods)
oc scale deployment/ghostwriter-classroom --replicas=0 -n ghostwriter-classroom
oc scale deployment/redis --replicas=0 -n ghostwriter-classroom

# Scale up to 1 (starts pods)
oc scale deployment/redis --replicas=1 -n ghostwriter-classroom
oc scale deployment/ghostwriter-classroom --replicas=1 -n ghostwriter-classroom

# Check pod status
oc get pods -n ghostwriter-classroom -w
```

## Resource Consumption

### When Active (1 replica each)

| Component | CPU Request | Memory Request | Idle CPU | Active CPU |
|-----------|-------------|----------------|----------|------------|
| Classroom | 250m | 512Mi | ~5m | 100-500m |
| Redis | 100m | 256Mi | ~2m | 10-50m |
| **TOTAL** | **350m** | **768Mi** | **~7m** | **110-550m** |

### When Scaled to Zero

| Component | CPU | Memory | Cost |
|-----------|-----|--------|------|
| All | 0m | 0Mi | **$0** |

## Production Recommendations

### For Demos/Workshops

- ✅ **Enable idle shutdown** (default)
- ⏱️ Set timeout to match demo length (e.g., 2 hours for workshop)
- 📊 Monitor via `/idle-status` endpoint
- 🔄 Scale down manually after events

### For Development

- ❌ **Disable idle shutdown** during active development
  ```yaml
  - name: ENABLE_IDLE_SHUTDOWN
    value: "false"
  ```
- Or increase timeout to 4+ hours

### For Always-On Production

- ❌ Disable idle shutdown
- ✅ Scale to 2-3 replicas for HA
- ✅ Use external Redis cluster
- 📊 Add monitoring/alerting

## Troubleshooting

### Pod keeps restarting

Check if idle timeout is too short:
```bash
oc describe pod -l app=ghostwriter-classroom -n ghostwriter-classroom
```

If you see exit code 0 repeatedly, increase timeout or disable:
```bash
oc set env deployment/ghostwriter-classroom IDLE_TIMEOUT_SECONDS=7200 -n ghostwriter-classroom
```

### Can't access after idle shutdown

The pod auto-restarts on new requests, but takes ~30 seconds. Be patient or pre-warm:
```bash
./openshift/scale-up.sh
```

### Activity not being tracked

Check middleware is working:
```bash
# Make a request
curl https://<route>/

# Check idle time (should reset to ~0)
curl https://<route>/idle-status
```

## Disable Idle Shutdown

### Temporary (until pod restart)

```bash
oc set env deployment/ghostwriter-classroom ENABLE_IDLE_SHUTDOWN=false -n ghostwriter-classroom
```

### Permanent

Edit `openshift/classroom-deployment.yaml`:
```yaml
- name: ENABLE_IDLE_SHUTDOWN
  value: "false"
```

Then redeploy:
```bash
oc apply -f openshift/classroom-deployment.yaml
```

## Cost Savings Example

**Scenario:** Workshop runs 2 hours per week

| Mode | Weekly Runtime | Monthly Cost* | Savings |
|------|----------------|---------------|---------|
| Always On | 168h | ~$12 | - |
| Idle Shutdown (1h) | 3h | ~$0.21 | **98%** |
| Manual Scale Down | 2h | ~$0.14 | **99%** |

*Estimated for typical cloud pricing. Actual costs vary by provider.

## Best Practices

1. ✅ **Keep default enabled** for cloud deployments
2. ⏱️ **Set timeout** slightly longer than typical demo length
3. 📊 **Monitor** idle status during active use
4. 🔧 **Scale down manually** after major events
5. 🧪 **Test timeout** with: `IDLE_TIMEOUT_SECONDS=300` (5 min)
6. 📝 **Document timeout** for your team
7. 🔔 **Add monitoring** if running 24/7

