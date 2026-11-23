# Image published to Docker Hub and run by the Hugging Face Space (port 8501).
# The pipeline tags it with a hash of the files copied below: keep that list
# in .github/workflows/pipeline.yml in step with the COPY lines.
FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY .streamlit/ ./.streamlit/
COPY src/ ./src/
COPY data/ ./data/

EXPOSE 8501

ENTRYPOINT ["streamlit", "run", "src/streamlit_app.py", "--server.port=8501", "--server.address=0.0.0.0"]
