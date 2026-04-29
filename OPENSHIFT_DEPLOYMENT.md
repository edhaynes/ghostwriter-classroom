# Ghostwriter Classroom - OpenShift Deployment Guide

## Prerequisites

1. **OpenShift CLI (`oc`)** installed
2. **Podman** installed  
3. **Groq API Key** (get from https://console.groq.com)
4. Access to an OpenShift cluster

## Quick Deployment

### Step 1: Login to OpenShift

```bash
oc login <your-cluster-url>
```

### Step 2: Configure Secrets

Edit `openshift/secret.yaml` and replace the API key:

```bash
nano openshift/secret.yaml
# Replace REPLACE_WITH_YOUR_GROQ_API_KEY with your actual key
```

### Step 3: Deploy

```bash
cd /Users/ehaynes/dnd2/ghostwriter_classroom
./deploy-openshift.sh
```

This script will:
- Create the `ghostwriter-classroom` namespace
- Deploy Redis with persistent storage
- Build the container image from source
- Push to OpenShift internal registry
- Deploy the classroom application
- Create a Route for external access

### Step 4: Access

After deployment, get the URL:

```bash
oc get route classroom -n ghostwriter-classroom -o jsonpath='{.spec.host}'
```

Visit `https://<route-url>` to use the classroom.

## Manual Deployment Steps

If you prefer to deploy manually:

```bash
# 1. Create namespace
oc apply -f openshift/namespace.yaml
oc project ghostwriter-classroom

# 2. Create secrets
oc apply -f openshift/secret.yaml

# 3. Deploy Redis
oc apply -f openshift/redis-deployment.yaml

# 4. Build and push image
podman build -t classroom:latest -f Containerfile .
REGISTRY=$(oc get route default-route -n openshift-image-registry -o jsonpath='{.spec.host}')
podman login -u $(oc whoami) -p $(oc whoami -t) $REGISTRY
podman tag classroom:latest $REGISTRY/ghostwriter-classroom/classroom:latest
podman push $REGISTRY/ghostwriter-classroom/classroom:latest

# 5. Deploy application
oc apply -f openshift/classroom-deployment.yaml

# 6. Get route
oc get route classroom
```

## Useful Commands

### Check status
```bash
oc get pods -n ghostwriter-classroom
oc get deployments -n ghostwriter-classroom
oc get routes -n ghostwriter-classroom
```

### View logs
```bash
# Classroom app
oc logs -f deployment/ghostwriter-classroom

# Redis
oc logs -f deployment/redis
```

### Scale up/down
```bash
oc scale deployment/ghostwriter-classroom --replicas=3
```

### Update configuration
```bash
# Edit secrets
oc edit secret classroom-secrets

# Restart deployment
oc rollout restart deployment/ghostwriter-classroom
```

### Delete everything
```bash
oc delete namespace ghostwriter-classroom
```

## Architecture

```
┌─────────────────────────────────────────┐
│        OpenShift Cluster                │
│                                         │
│  ┌──────────────────────────────────┐  │
│  │  ghostwriter-classroom namespace │  │
│  │                                  │  │
│  │  ┌────────────────┐              │  │
│  │  │  Classroom App │              │  │
│  │  │  (port 8081)   │◄─────┐       │  │
│  │  └────────────────┘      │       │  │
│  │          │                │       │  │
│  │          ▼                │       │  │
│  │  ┌────────────────┐      │       │  │
│  │  │     Redis      │      │       │  │
│  │  │  (port 6379)   │      │       │  │
│  │  └────────────────┘      │       │  │
│  │          │                │       │  │
│  │          ▼                │       │  │
│  │  ┌────────────────┐      │       │  │
│  │  │  PVC (1GB)     │      │       │  │
│  │  └────────────────┘      │       │  │
│  │                           │       │  │
│  │                    ┌──────┴────┐ │  │
│  │                    │   Route   │ │  │
│  │                    │ (HTTPS)   │ │  │
│  │                    └───────────┘ │  │
│  └──────────────────────────────────┘  │
└─────────────────────────────────────────┘
             │
             ▼
      ┌──────────────┐
      │   Internet   │
      │   (Users)    │
      └──────────────┘
```

## Environment Variables

The classroom app uses:

- `REDIS_URL`: Redis connection string (default: `redis://redis:6379`)
- `GROQ_API_KEY`: Your Groq API key for AI features
- `CLASSROOM_KSERVE_ENDPOINT`: LLM endpoint (default: Groq)
- `CLASSROOM_MODEL`: LLM model (default: `llama-3.3-70b-versatile`)

## Troubleshooting

### Pods not starting
```bash
oc describe pod <pod-name>
oc logs <pod-name>
```

### Image pull errors
```bash
# Check image stream
oc get imagestream

# Rebuild and push
./deploy-openshift.sh
```

### Redis connection issues
```bash
# Test Redis connection
oc exec -it deployment/ghostwriter-classroom -- sh -c "curl -v redis:6379"
```

### Route not accessible
```bash
# Check route
oc get route classroom -o yaml

# Check if TLS is configured
oc describe route classroom
```

## Production Recommendations

1. **Scale Redis**: Use Redis Operator or external Redis cluster for HA
2. **Resource limits**: Adjust CPU/memory based on usage
3. **Replicas**: Scale classroom deployment to 2-3 for availability
4. **Monitoring**: Add Prometheus metrics and Grafana dashboards
5. **Backup**: Schedule PVC backups for Redis data
6. **Security**: Use NetworkPolicies to restrict traffic
7. **Secrets**: Use Sealed Secrets or Vault for API keys

