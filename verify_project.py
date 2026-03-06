import os
from omr_processor import process_omr

# The sample image I generated for you
sample_img = r"C:\Users\Prasanna\.gemini\antigravity\brain\bcc84d52-4e8f-44c7-8bb5-ba1f4be39534\sample_omr_sheet_mockup_1772326331264.png"
output_img = "test_result.jpg"

# The answer key we set up (1:A, 2:B, 3:C, 4:D, 5:A)
answer_key = {1: 'A', 2: 'B', 3: 'C', 4: 'D', 5: 'A'}

if not os.path.exists(sample_img):
    print(f"Error: Sample image not found at {sample_img}")
else:
    print("--- STARTING OMR EVALUATION TEST ---")
    try:
        score, total, selected, final_path = process_omr(sample_img, answer_key, output_img)
        print(f"TEST SUCCESSFUL!")
        print(f"Score: {score} / {total}")
        print(f"Percentage: {(score/total)*100}%")
        print(f"Detected Answers: {selected}")
        print(f"Processed image saved to: {os.path.abspath(output_img)}")
    except Exception as e:
        print(f"TEST FAILED: {str(e)}")
