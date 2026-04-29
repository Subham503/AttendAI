from deepface import DeepFace

def verify_face(img1_path, img2_path):
    result = DeepFace.verify(img1_path, img2_path, enforce_detection=False)
    return result['verified']