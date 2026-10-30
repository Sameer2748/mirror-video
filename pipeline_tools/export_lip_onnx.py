#!/usr/bin/env python3
"""export_lip_onnx.py -- export the two lip-scan networks to ONNX, once (Step 4B.6, for Windows without an NVIDIA GPU).

  python export_lip_onnx.py [--talknet DIR] [--out DIR]          (default out: ../data/models_onnx)

Writes s3fd_head.onnx (the S3FD forward pass of lipgpu.py, up to the raw loc/conf maps; boxes and NMS stay in Python) and
talknet.onnx (TalkNet: audio MFCC + face crops -> audio-visual score and visual score per frame, exactly what lipgpu.py
collects). Same trained weights, FP32. Then checks both files against PyTorch at several input sizes and prints the
largest difference (must be < 1e-3); lipgpu.py --engine onnx uses them (any computer; auto on Windows without NVIDIA).
Needs: torch 2.5+ (CPU build is fine), onnx, onnxscript, onnxruntime (any flavour)."""
import argparse, os, sys
import numpy as np
np.int = int
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--talknet", default=os.path.join(HERE, "..", "third_party", "TalkNet-ASD"))
    p.add_argument("--out", default=os.path.join(HERE, "..", "data", "models_onnx"))
    a = p.parse_args()
    import torch
    import lipdetect as L
    from lipgpu import build_head_class
    os.makedirs(a.out, exist_ok=True)
    tn = os.path.abspath(a.talknet); cwd = os.getcwd(); os.chdir(tn); model, head, det = L.load_talknet(tn); os.chdir(cwd)
    torch.set_num_threads(max(1, (os.cpu_count() or 4) - 2))

    # S3FD: image (1, 3, H, W) = BGR minus the mean, as lipgpu.detect_one builds it -> 6 loc + 6 conf maps
    hd = build_head_class()(det).eval()
    outs = [f"loc{i}" for i in range(6)] + [f"conf{i}" for i in range(6)]
    dyn = {"img": {2: "h", 3: "w"}}; dyn.update({n: {1: f"{n}_h", 2: f"{n}_w"} for n in outs})
    p_s3 = os.path.join(a.out, "s3fd_head.onnx")
    torch.onnx.export(hd, torch.randn(1, 3, 270, 480), p_s3, input_names=["img"], output_names=outs, dynamic_axes=dyn,
                      opset_version=17, do_constant_folding=True, dynamo=False)

    # TalkNet: audio MFCC (1, Ta, 13) + face crops (1, Tv, 112, 112), Ta = 4 Tv -> AV score, visual score per frame
    class TalkNetONNX(torch.nn.Module):
        def __init__(s, m, h): super().__init__(); s.m, s.h = m, h
        def forward(s, A, V):
            eA = s.m.forward_audio_frontend(A); eV = s.m.forward_visual_frontend(V)
            eA, eV = s.m.forward_cross_attention(eA, eV)
            # the backends already return (frames, 256) / (frames, 128): lossAV's x.squeeze(1) is a no-op there and is left
            # out (in ONNX it becomes a conditional node that breaks shape inference)
            av = s.h.FC(s.m.forward_audio_visual_backend(eA, eV))               # = lossAV.forward(labels=None) before numpy
            ov = s.h.vhead.FC(s.m.forward_visual_backend(eV))
            return av[:, 1], ov[:, 1] - ov[:, 0]
    tw = TalkNetONNX(model, head).eval()
    p_tn = os.path.join(a.out, "talknet.onnx")
    # the newer exporter (dynamo) is needed here: the old one freezes the frame count inside the attention layers
    from torch.export import Dim
    tv = Dim("tv", min=2, max=1000)
    torch.onnx.export(tw, (torch.randn(1, 200, 13), torch.rand(1, 50, 112, 112) * 255), p_tn, input_names=["A", "V"],
                      output_names=["av", "vis"], dynamic_shapes={"A": {1: 4 * tv}, "V": {1: tv}}, dynamo=True, opset_version=18)

    # check against PyTorch at the sizes the scan uses (chunks of 1, 2, 4 s + a short last chunk; two frame shapes)
    import onnxruntime as ort
    s3 = ort.InferenceSession(p_s3, providers=["CPUExecutionProvider"]); tk = ort.InferenceSession(p_tn, providers=["CPUExecutionProvider"])
    worst = 0.0
    for h, w in ((270, 480), (480, 270), (270, 270)):
        x = torch.randn(1, 3, h, w) * 50
        with torch.no_grad(): ref = [t.numpy() for t in hd(x)]
        got = s3.run(None, {"img": x.numpy()})
        d = max(float(np.abs(r - g).max()) for r, g in zip(ref, got)); worst = max(worst, d); print(f"S3FD {h}x{w}: max diff {d:.2e}")
    for tv in (25, 50, 100, 13, 1):
        A = torch.randn(1, 4 * tv, 13); V = torch.rand(1, tv, 112, 112) * 255
        with torch.no_grad(): ref = [t.numpy() for t in tw(A, V)]
        got = tk.run(None, {"A": A.numpy(), "V": V.numpy()})
        d = max(float(np.abs(r - g).max()) for r, g in zip(ref, got)); worst = max(worst, d); print(f"TalkNet {tv} frames: max diff {d:.2e}")
    print(f"saved {p_s3} and {p_tn}; largest difference {worst:.2e} -> " + ("OK" if worst < 1e-3 else "TOO LARGE: do not use ONNX"))
    sys.exit(0 if worst < 1e-3 else 1)


if __name__ == "__main__":
    main()
