#!/bin/bash
# Scale up classroom for use

echo "Scaling up Ghostwriter Classroom..."

oc scale deployment/redis --replicas=1 -n ghostwriter-classroom
sleep 5
oc scale deployment/ghostwriter-classroom --replicas=1 -n ghostwriter-classroom

echo "✓ Scaled to 1 replica (service active)"
echo ""
echo "Waiting for pods to be ready..."
oc wait --for=condition=available --timeout=120s deployment/ghostwriter-classroom -n ghostwriter-classroom

ROUTE=$(oc get route classroom -n ghostwriter-classroom -o jsonpath='{.spec.host}')
echo ""
echo "✅ Classroom is ready!"
echo "📍 URL: https://$ROUTE"
