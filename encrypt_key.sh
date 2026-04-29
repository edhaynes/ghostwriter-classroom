#!/bin/bash
# Encrypt GROQ_API_KEY for secure sharing

set -e

if [ -z "$GROQ_API_KEY" ]; then
    echo "Error: GROQ_API_KEY environment variable not set"
    exit 1
fi

echo "Encrypting GROQ_API_KEY..."
echo ""
echo "Enter a strong password (you'll share this separately):"
read -s PASSWORD

echo ""
echo "Confirm password:"
read -s PASSWORD_CONFIRM

if [ "$PASSWORD" != "$PASSWORD_CONFIRM" ]; then
    echo "Passwords don't match!"
    exit 1
fi

# Encrypt using AES-256-CBC with strong key derivation
echo -n "$GROQ_API_KEY" | openssl enc -aes-256-cbc -a -pbkdf2 -iter 100000 -pass pass:"$PASSWORD" > groq_key_encrypted.txt

echo ""
echo "✅ Encrypted key saved to: groq_key_encrypted.txt"
echo ""
cat groq_key_encrypted.txt
echo ""
echo "📧 Send groq_key_encrypted.txt to your Linux expert"
echo "🔐 Send the password separately (Signal, phone call, etc.)"
echo ""
echo "Decryption command for recipient:"
echo "  openssl enc -aes-256-cbc -d -a -pbkdf2 -iter 100000 -in groq_key_encrypted.txt -pass pass:'YOUR_PASSWORD'"
echo ""
