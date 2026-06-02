import cv2
import numpy as np

def verify_liveness(frame):
    """
    Simple liveness check:
    - Checks brightness + blur + face presence
    """

    if frame is None:
        return False

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # 1. Blur check (fake images often sharp or too blurry)
    laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
    if laplacian_var < 30:
        return False

    # 2. Brightness check
    brightness = np.mean(gray)
    if brightness < 40 or brightness > 220:
        return False

    # 3. Face detection check
    face_cascade = cv2.CascadeClassifier("haarcascade_frontalface_default.xml")
    faces = face_cascade.detectMultiScale(gray, 1.3, 5)

    if len(faces) == 0:
        return False

    return True