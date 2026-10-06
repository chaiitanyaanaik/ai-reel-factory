# Thin app image — rebuild this often after code / frontend/dist changes.
# Requires base image on Hub (or local):
#   docker build -f Dockerfile.base --platform linux/amd64 -t chaiitanyaanaik/reelkut-base:latest .
#   docker push chaiitanyaanaik/reelkut-base:latest
#
# App:
#   cd frontend && npm run build && cd ..
#   docker build --platform linux/amd64 -t chaiitanyaanaik/reelkut:latest .
#   docker push chaiitanyaanaik/reelkut:latest

ARG BASE_IMAGE=chaiitanyaanaik/reelkut-base:latest
FROM ${BASE_IMAGE}

WORKDIR /app

COPY . .

RUN test -f frontend/dist/index.html || \
    (echo "ERROR: frontend/dist missing. Run: cd frontend && npm ci && npm run build" && exit 1)

EXPOSE 8000

VOLUME ["/app/projects"]

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
