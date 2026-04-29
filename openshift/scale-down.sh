#!/bin/bash
# Scale down classroom to save resources

echo "Scaling down Ghostwriter Classroom..."

oc scale deployment/ghostwriter-classroom --replicas=0 -n ghostwriter-classroom
oc scale deployment/redis --replicas=0 -n ghostwriter-classroom

echo "✓ Scaled to 0 replicas (resources freed)"
echo ""
echo "To scale back up, run: ./scale-up.sh"
