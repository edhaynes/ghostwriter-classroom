#!/bin/bash
set -e

echo "================================"
echo "Ghostwriter Classroom - OpenShift Deployment"
echo "================================"

# Check if logged in
if ! oc whoami &> /dev/null; then
    echo "❌ Not logged in to OpenShift. Please run: oc login <cluster-url>"
    exit 1
fi

echo "✓ Logged in as: $(oc whoami)"

# Create namespace
echo ""
echo "📦 Creating namespace..."
oc apply -f openshift/namespace.yaml

# Set current namespace
oc project ghostwriter-classroom

# Create secret (you need to edit this file first!)
echo ""
echo "🔐 Creating secrets..."
if grep -q "REPLACE_WITH_YOUR_GROQ_API_KEY" openshift/secret.yaml; then
    echo "⚠️  WARNING: Please edit openshift/secret.yaml and add your Groq API key first!"
    echo "   Then run this script again."
    exit 1
fi
oc apply -f openshift/secret.yaml

# Deploy Redis
echo ""
echo "📊 Deploying Redis..."
oc apply -f openshift/redis-deployment.yaml

# Build container image in OpenShift
echo ""
echo "🔨 Building container image in OpenShift..."
# Create build config if it doesn't exist
if ! oc get bc classroom &> /dev/null; then
    oc new-build --name classroom --binary --strategy=docker
fi
# Start build from local directory
oc start-build classroom --from-dir=. --follow

# Deploy classroom app
echo ""
echo "🚀 Deploying Ghostwriter Classroom..."
oc apply -f openshift/classroom-deployment.yaml

# Wait for deployment
echo ""
echo "⏳ Waiting for deployment to be ready..."
oc wait --for=condition=available --timeout=300s deployment/ghostwriter-classroom

# Get route
echo ""
echo "================================"
echo "✅ Deployment complete!"
echo "================================"
ROUTE=$(oc get route classroom -o jsonpath='{.spec.host}')
echo ""
echo "📍 Classroom URL: https://$ROUTE"
echo ""
echo "To check status:"
echo "  oc get pods -n ghostwriter-classroom"
echo ""
echo "To view logs:"
echo "  oc logs -f deployment/ghostwriter-classroom"
echo ""
