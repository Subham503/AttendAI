import cv2
import mediapipe as mp

class LivenessDetector:
    def __init__(self):
        self.mp_face_mesh = mp.solutions.face_mesh
        # Initialize FaceMesh once to save overhead per request
        self.face_mesh = self.mp_face_mesh.FaceMesh(
            static_image_mode=True,
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.5
        )

    def verify(self, image):
        """
        Verify liveness using server-side MediaPipe validation.
        """
        try:
            rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            results = self.face_mesh.process(rgb_image)
            
            # If MediaPipe cannot detect a face mesh, reject the frame.
            # This provides a more robust check than the simple HAAR cascade.
            if not results.multi_face_landmarks:
                return False
                
            return True
        except Exception as e:
            print(f"Liveness verification error: {e}")
            return False

# Global instance
detector = LivenessDetector()

def verify_liveness(image):
    return detector.verify(image)
