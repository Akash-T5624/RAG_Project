# PDF RAG Application

This application lets you ask questions about a PDF document.

## How to Run

1. Install the required Python packages:

   ```powershell
   python -m pip install -r requirements.txt
   ```

2. Create a file named `.env` in the project folder and add your Groq API key:

   ```env
   GROQ_API_KEY=your_groq_api_key_here
   ```

3. Put your PDF file inside the `documents` folder.

4. In `pdf_reader.py`, set the PDF file path if needed:

   ```python
   pdf_file = "documents/your_file.pdf"
   ```

5. Run the application:

   ```powershell
   python main.py
   ```

6. Type a question about the PDF. Type `exit` to close the application.

## Workflow

```text
PDF file
   |
   v
pdf_reader.py reads the PDF and creates vectors
   |
   v
query.py finds relevant PDF content for your question
   |
   v
Groq Llama model generates an answer
```

`main.py` automatically runs `pdf_reader.py` first. If PDF processing succeeds,
it starts `query.py` for asking questions.
