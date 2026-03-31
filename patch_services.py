import os

file_path = "src/nuki_integration/services.py"
with open(file_path, "r") as f:
    content = f.read()

content = content.replace("Studio Access Management", "TWENTY4SEVEN-GYM")

with open(file_path, "w") as f:
    f.write(content)
print("SUCCESS: services.py updated with new brand name.")
