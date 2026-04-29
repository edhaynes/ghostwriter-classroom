#!/bin/bash
# Deploy Ghostwriter Classroom to Google Cloud Run
# Usage: ./deploy-gcloud.sh

set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Configuration
PROJECT_ID="${GCLOUD_PROJECT:-llmdm-game}"
GCLOUD_REGION="us-central1"
GCLOUD_SERVICE="ghostwriter-classroom"

echo -e "${BLUE}☁️  Deploying Ghostwriter Classroom to Google Cloud Run${NC}"
echo "   Project: $PROJECT_ID"
echo "   Region: $GCLOUD_REGION"
echo "   Service: $GCLOUD_SERVICE"
echo ""

# Check gcloud CLI
if ! command -v gcloud &> /dev/null; then
    echo -e "${RED}❌ gcloud CLI not found. Install with: brew install google-cloud-sdk${NC}"
    exit 1
fi

# Set project
echo "📋 Setting project..."
gcloud config set project $PROJECT_ID

# Enable required APIs
echo "🔌 Enabling required APIs..."
gcloud services enable \
    run.googleapis.com \
    artifactregistry.googleapis.com

# Check if GROQ_API_KEY secret exists
echo "🔐 Checking secrets..."
if ! gcloud secrets describe GROQ_API_KEY >/dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  GROQ_API_KEY secret not found.${NC}"
    echo "   Creating secret (you'll need to add the value separately)..."
    echo -n "placeholder" | gcloud secrets create GROQ_API_KEY --data-file=- --replication-policy=automatic
    echo ""
    echo -e "${YELLOW}📝 Update the secret with your actual Groq API key:${NC}"
    echo "   echo -n 'gsk_...' | gcloud secrets versions add GROQ_API_KEY --data-file=-"
    echo ""
    read -p "Press Enter when ready to continue..."
fi

# Deploy to Cloud Run using Containerfile
echo "📦 Deploying to Cloud Run..."
gcloud run deploy $GCLOUD_SERVICE \
    --source . \
    --region $GCLOUD_REGION \
    --platform managed \
    --allow-unauthenticated \
    --port 8081 \
    --memory 2Gi \
    --cpu 1 \
    --min-instances 0 \
    --max-instances 3 \
    --timeout 300 \
    --set-secrets="GROQ_API_KEY=GROQ_API_KEY:latest"

# Get service URL
echo ""
echo -e "${GREEN}✅ Deployment complete!${NC}"
echo ""

SERVICE_URL=$(gcloud run services describe $GCLOUD_SERVICE \
    --region $GCLOUD_REGION \
    --format 'value(status.url)')

echo "🌐 Service URL: $SERVICE_URL"
echo ""
echo "Test endpoints:"
echo "  curl $SERVICE_URL/health"
echo "  open $SERVICE_URL"
echo ""
echo "View logs:"
echo "  gcloud run services logs read $GCLOUD_SERVICE --region $GCLOUD_REGION --limit 50"
echo ""
echo "Scale down to zero:"
echo "  gcloud run services update $GCLOUD_SERVICE --region $GCLOUD_REGION --min-instances 0"
echo ""
