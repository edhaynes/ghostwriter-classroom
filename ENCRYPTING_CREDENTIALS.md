# Encrypting API Keys for Secure Sharing

When you need to share your GROQ_API_KEY (or other credentials) with someone, use encryption instead of sending plain text.

## For the Sender (You)

1. **Encrypt your key:**
   ```bash
   ./encrypt_key.sh
   ```
   - You'll be prompted to enter a password twice
   - Creates `groq_key_encrypted.txt`

2. **Send the encrypted file:**
   - Email, Slack, or any file transfer: `groq_key_encrypted.txt`

3. **Share the password separately:**
   - Use a different channel: Signal, phone call, SMS, etc.
   - **Never** send password and encrypted file together!

## For the Recipient (Linux Expert)

1. **Save the encrypted file:**
   - Save `groq_key_encrypted.txt` to your directory

2. **Decrypt using the script:**
   ```bash
   ./decrypt_key.sh
   ```
   - Enter the password when prompted
   - Follow on-screen instructions to set environment variable

3. **Or decrypt manually:**
   ```bash
   openssl enc -aes-256-cbc -d -a -pbkdf2 -iter 100000 \
     -in groq_key_encrypted.txt -pass pass:'YOUR_PASSWORD'
   ```

## Security Notes

- **AES-256-CBC** encryption with 100,000 PBKDF2 iterations
- Password never stored, only used during encryption/decryption
- Encrypted file is safe to send via email/Slack
- Always use a strong, unique password
- Delete encrypted file after recipient confirms receipt

## Example Workflow

1. You: `./encrypt_key.sh` → creates `groq_key_encrypted.txt`
2. You: Email the file to recipient
3. You: Text/call recipient with password: "MyStr0ngP@ssw0rd!"
4. Recipient: `./decrypt_key.sh` → enters password → gets API key
5. Both: Delete encrypted file and password from messages
