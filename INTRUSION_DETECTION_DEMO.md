# Network Intrusion Detection Demo — Instructor Guide

## Quick Start

📊 **Presentation slides** are linked from the lobby screen (presentation icon next to the title)

## Option 1: Automated Simulation (No Students)

Perfect for testing or demonstrations when students aren't available.

1. **Create Session**
   - Click "New Instructor Session"
   - Select **"Intrusion Detection"** mode
   - Session code will be displayed (e.g., `A3F`)

2. **Configure**
   - Enter your **Groq API Key** (get one free at console.groq.com)
   - Select models for generation and grading (recommend `llama-3.3-70b-versatile`)
   - Click **"Generate Network Traffic"** (choose difficulty: easy/medium/hard)
   - Optionally customize the detection template and rubric

3. **Run Simulation**
   - Set number of AI students (2-40)
   - Set submission delay (seconds between submissions)
   - Click **"Run Full Simulation"**
   - Watch as AI students submit detection scripts, get graded, and results appear

4. **Review & Polish**
   - AI grading happens automatically during review phase
   - In **Reveal** phase, select two models to compare polish improvements
   - Click "View Network Traffic" to see the actual attack data

---

## Option 2: Live Students

For classroom use with real students.

### ⚠️ **CRITICAL TIMING RULES**

- **Students MUST join BEFORE you start the sprint**
- **Students MUST submit within the allotted time** (default 5 minutes)
- Late submissions are not accepted

### Steps

1. **Create Session**
   - Click "New Instructor Session"
   - Select **"Intrusion Detection"** mode
   - Session code will be displayed (e.g., `A3F`)

2. **Share Session Code**
   - **Share the session code with students NOW**
   - Students navigate to the classroom URL and enter the code
   - Monitor the "X students connected" counter

3. **Configure (while students join)**
   - Enter your **Groq API Key**
   - Select models for generation and grading
   - Click **"Generate Network Traffic"** (this creates the attack data students will analyze)
   - Optionally customize the detection template and rubric

4. **Start Writing Phase**
   - ⚠️ **WAIT until all students have joined**
   - Click **"▶ Start 5-Minute Sprint"**
   - Timer starts for all students simultaneously
   - Students write detection logic to identify the network intrusion

5. **Monitor Submissions**
   - Watch as students submit their detection scripts
   - Students who don't submit within the time limit are marked as incomplete

6. **Review Phase (Automatic)**
   - AI grades all submissions automatically
   - Results appear in real-time
   - You can click any entry to view the full detection logic

7. **Reveal Phase**
   - Final scores and AI winner displayed
   - Use **2-model polish comparison** to show iterative improvement
   - Click "View Network Traffic" to show students the actual attack data
   - Discuss what made the winning detection effective

---

## Tips

- **Test run**: Do a simulation first to familiarize yourself with the flow
- **API Key**: Groq offers free tier — sufficient for classroom use
- **Traffic Difficulty**:
  - Easy: Simple port scan (5 events)
  - Medium: Port scan with IP spoofing (10-15 events)
  - Hard: Complex multi-stage attack (20+ events)
- **Ollama Cloud**: You can also use cloud-hosted Ollama instances with custom endpoints
- **Polish Models**: Compare different model sizes (e.g., 70B vs 8B) to show capability differences

## Troubleshooting

- **"Please generate network traffic first"**: You forgot step 2.3 — click "Generate Network Traffic"
- **Students can't join**: Make sure they're joining BEFORE you start the sprint
- **No submissions**: Verify the timer didn't expire before students could submit
- **AI grading slow**: Use faster models like `llama-3.1-8b-instant` for grading

---

## Flow Summary

```
Setup (instructor generates traffic, students join)
   ↓
Writing (5-min timer, students submit detection logic)
   ↓
Review (AI grades all submissions)
   ↓
Reveal (show results, run polish comparison, view traffic)
```
