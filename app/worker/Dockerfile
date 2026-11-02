# Dub Studio pipeline worker: ANY step of the pipeline, as a RunPod serverless job.
#
# The lip-scan image proved the shape; this one carries the whole pipeline, because a deployed API box can run
# none of the 37 steps by itself. Everything is baked in -- scripts, TalkNet, mediapipe, the two models the
# pipeline actually uses (faster-whisper-small and paraphrase-multilingual-MiniLM-L12-v2) and the subtitle font --
# so a job downloads nothing but the episode's own files. Five bare-pod failures taught us that lesson.
#
# The job is a command the app already builds (`pipeline/steps.py`), with this machine's paths swapped for
# placeholders; see dubstudio/cloud.py `tokenise`. The pipeline scripts themselves are untouched.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl xz-utils libgl1 libglib2.0-0 fontconfig \
    && rm -rf /var/lib/apt/lists/*

# ffmpeg 8.1, to match the Mac's 8.1.2 -- NOT Debian's apt ffmpeg. Measured, not assumed: on ep34's dub MP3 the
# older apt build reports the duration 51 ms longer than the Mac does (664686 vs 664635 ms), and the pipeline turns
# that straight into `manifest.json`'s `total_ms`, which is where the last clip ends. 51 ms is over a frame at
# 25 fps. A worker is meant to give the SAME answer, so the probe has to agree.
RUN curl -fsSL -o /tmp/ff.tar.xz \
      "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz" \
    && tar -xJf /tmp/ff.tar.xz -C /tmp \
    && mv /tmp/ffmpeg-n8.1*/bin/ffmpeg /tmp/ffmpeg-n8.1*/bin/ffprobe /usr/local/bin/ \
    && rm -rf /tmp/ff.tar.xz /tmp/ffmpeg-n8.1* \
    && ffmpeg -version | head -1 && ffprobe -version | head -1

# CPU wheels come from PyTorch's own index; everything else from PyPI. Two steps on purpose: one `pip install`
# with --index-url makes pip look for *every* package there, and `runpod` is not on it.
# TWO pythons, exactly as the Mac has (the app's python and `pipeline_env`), and for the same reason it turns out
# to be necessary: the RunPod SDK pulls opentelemetry, which needs protobuf >= 5, while mediapipe 0.10.14 pins
# protobuf < 5. One environment cannot hold both -- `pip` says ResolutionImpossible. So the handler runs on the
# system python with the SDK, and the pipeline runs as a subprocess in /opt/pipeline_env, which is what {PY} means.
RUN pip install --no-cache-dir runpod

RUN python -m venv /opt/pipeline_env \
    && /opt/pipeline_env/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/pipeline_env/bin/pip install --no-cache-dir \
         torch==2.5.1 torchaudio==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cpu
# The versions the Mac's pipeline_env actually runs (`app/scripts/setup_pipeline.sh` installs them unpinned, which
# is fine on one machine but not here: the point of a worker is that it gives the SAME answer). Pinning also stops
# pip backtracking for ten minutes through onnxruntime and faster-whisper releases looking for a numpy<2 fit.
# torch is the exception: 2.14 has no CPU wheel for this base, and the lip scan came back byte-identical on 2.5.1.
RUN /opt/pipeline_env/bin/pip install --no-cache-dir \
      numpy==1.26.4 scipy==1.17.1 opencv-python-headless==4.11.0.86 mediapipe==0.10.14 \
      faster-whisper==1.2.1 ctranslate2==4.8.3 onnxruntime==1.31.0 \
      sentence-transformers==6.1.0 transformers==5.19.0 scikit-learn==1.9.1 scenedetect==0.7.2 \
      python_speech_features imageio-ffmpeg psutil openpyxl pillow python-docx

COPY bundle.tar.gz models.tar.gz scene.tar.gz app.tar.gz /tmp/
RUN mkdir -p /opt/dubstudio /opt/models /opt/scene /opt/app \
    && tar -xzf /tmp/bundle.tar.gz -C /opt/dubstudio \
    && tar -xzf /tmp/models.tar.gz -C /opt/models \
    && tar -xzf /tmp/scene.tar.gz  -C /opt/scene \
    && tar -xzf /tmp/app.tar.gz    -C /opt/app \
    && rm /tmp/*.tar.gz \
    && test -f /opt/dubstudio/scripts/dubstitch.py \
    && test -d /opt/models/whisper \
    && ls /opt/scene/pipeline | head -3

COPY Poppins-Bold.ttf /opt/fonts/Poppins-Bold.ttf
RUN mkdir -p /usr/share/fonts/truetype/poppins \
    && cp /opt/fonts/Poppins-Bold.ttf /usr/share/fonts/truetype/poppins/ && fc-cache -f

COPY handler.py /handler.py
ENV PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
CMD ["python", "-u", "/handler.py"]
