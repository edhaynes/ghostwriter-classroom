#!/bin/bash
# Decrypt GROQ_API_KEY (for recipient)

set -e

if [ ! -f "groq_key_encrypted.txt" ]; then
    echo "Error: groq_key_encrypted.txt not found"
    exit 1
fi

echo "Enter the password (provided separately):"
read -s PASSWORD

echo ""
echo "Decrypting..."

DECRYPTED_KEY=$(openssl enc -aes-256-cbc -d -a -pbkdf2 -iter 100000 -in groq_key_encrypted.txt -pass pass:"$PASSWORD")

if [ $? -eq 0 ]; then
    echo "✅ Decryption successful!"
    echo ""
    echo "Add to your environment:"
    echo "  export GROQ_API_KEY='$DECRYPTED_KEY'"
    echo ""
    echo "Or add to ~/.bashrc or ~/.zshrc:"
    echo "  echo \"export GROQ_API_KEY='$DECRYPTED_KEY'\" >> ~/.bashrc"
else
    echo "❌ Decryption failed. Check your password."
    exit 1
fi
