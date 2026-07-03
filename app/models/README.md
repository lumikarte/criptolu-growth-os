# Modelos ONNX

## YuNet (auto-crop face-tracked, CRI-128)

`face_detection_yunet_2023mar.onnx` (~345 KB) — detector de caras de OpenCV Zoo, usado por
`services/framing.py` vía `cv2.FaceDetectorYN`. Sin PyTorch ni GPU.

**Descargar** (una vez) desde OpenCV Zoo y dejarlo en esta carpeta con ese nombre:

```
https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
```

La ruta se configura en `config.AUTOCROP_MODEL`. Si el archivo no está (o falta
`opencv-python-headless`, o el flag `AUTOCROP_ENABLED` está apagado), el corte de clips cae
al crop central de siempre — el auto-crop nunca rompe el pipeline.
