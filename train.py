import cv2
import os
import numpy as np

path = 'images'

recognizer = cv2.face.LBPHFaceRecognizer_create()
faces = []
ids = []

for file in os.listdir(path):
    img = cv2.imread(os.path.join(path, file), cv2.IMREAD_GRAYSCALE)

    id = int(file.split('_')[0])   # 🔥 important

    faces.append(img)
    ids.append(id)

recognizer.train(faces, np.array(ids))
recognizer.save('trainer.yml')

print("✅ Training done")