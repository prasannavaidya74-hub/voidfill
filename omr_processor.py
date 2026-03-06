import cv2
import numpy as np
import imutils
from imutils import contours

def load_and_resize_image(path):
    image = cv2.imread(path)
    if image is None:
        raise ValueError("Could not read the image.")
    # Assuming height=1000 for standard format, but keeping aspect ratio
    image = imutils.resize(image, height=1000)
    return image

def preprocess_image(image):
    # Version A: Standard grayscale for finding the boxes (sees all lines/borders)
    gray_contours = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    
    # Version B: Red channel to maximize contrast of BLUE student marks
    (B, G, R) = cv2.split(image)
    gray_bubbles = R 
    
    # Edge detection for finding the grid container
    blurred = cv2.GaussianBlur(gray_contours, (5, 5), 0)
    edged = cv2.Canny(blurred, 50, 150)
    
    return gray_contours, gray_bubbles, edged

def find_omr_contour(edged):
    cnts = cv2.findContours(edged.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cnts = imutils.grab_contours(cnts)
    docCnt = None

    if len(cnts) > 0:
        cnts = sorted(cnts, key=cv2.contourArea, reverse=True)
        for c in cnts:
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.02 * peri, True)
            if len(approx) == 4:
                docCnt = approx
                break
    return docCnt

def order_points(pts):
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect

def perspective_transform(original, contour):
    pts = contour.reshape(4, 2)
    rect = order_points(pts)
    (tl, tr, br, bl) = rect

    widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
    widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
    maxWidth = max(int(widthA), int(widthB))

    heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
    heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
    maxHeight = max(int(heightA), int(heightB))

    dst = np.array([
        [0, 0],
        [maxWidth - 1, 0],
        [maxWidth - 1, maxHeight - 1],
        [0, maxHeight - 1]], dtype="float32")

    M = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(original, M, (maxWidth, maxHeight))
    return warped

def threshold_image(warped_gray):
    # Otsu's thresholding
    thresh = cv2.threshold(warped_gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    
    # Clean up small noise with morphology
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
    
    return thresh

def detect_bubbles(thresh):
    # Find ALL contours
    cnts = cv2.findContours(thresh.copy(), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    cnts = imutils.grab_contours(cnts)
    questionCnts = []
    
    # 1. Bubble filtering - Use shape and size to find potential circles/text
    for c in cnts:
        (x, y, w, h) = cv2.boundingRect(c)
        ar = w / float(h)
        area = cv2.contourArea(c)
        bbox_area = w * h
        solidity = area / float(bbox_area) if bbox_area > 0 else 0
        
        # Bubbles and question numbers are usually small chunks
        if 8 <= w <= 120 and 8 <= h <= 120 and 0.4 <= ar <= 2.5 and solidity > 0.35:
            questionCnts.append(c)

    if len(questionCnts) == 0:
        return []

    # 2. Row Grouping
    questionCnts = contours.sort_contours(questionCnts, method="top-to-bottom")[0]
    
    rows = []
    if questionCnts:
        current_row = [questionCnts[0]]
        for i in range(1, len(questionCnts)):
            (_, y_prev, _, h_prev) = cv2.boundingRect(questionCnts[i-1])
            (_, y_curr, _, _) = cv2.boundingRect(questionCnts[i])
            if abs(y_curr - y_prev) < (h_prev * 0.8):
                current_row.append(questionCnts[i])
            else:
                rows.append(current_row)
                current_row = [questionCnts[i]]
        rows.append(current_row)

    # Return grouped rows (sorted left-to-right internally)
    final_rows = []
    for row in rows:
        final_rows.append(contours.sort_contours(row, method="left-to-right")[0])
    
    return final_rows

def evaluate_answers(thresh, rows, answer_key):
    score = 0
    selected_answers = {}
    options_map = {0: 'A', 1: 'B', 2: 'C', 3: 'D'}
    
    # Use the full width of the 'thresh' image (the warped box) to define vertical zones.
    # This prevents misalignment if a bubble (like the Q.No text) is not detected in a row.
    warped_width = thresh.shape[1]
    row_data_for_marking = []

    for (q, cnts) in enumerate(rows):
        question_number = q + 1
        
        # Track detections in this row
        bubbled_col = None
        max_fill_ratio = 0
        row_bubbles = [None] * 4 # A, B, C, D

        for c in cnts:
            (x, y, w, h) = cv2.boundingRect(c)
            center_x = x + (w / 2)
            
            # Use the global warped box width for mapping
            relative_x = center_x / float(warped_width)
            
            # Column mapping (assuming the 5-column layout)
            # 0.0-0.2: QNo, 0.2-0.4: A, 0.4-0.6: B, 0.6-0.8: C, 0.8-1.0: D
            if relative_x < 0.22:
                col_idx = -1 # Question Number
            elif relative_x < 0.42:
                col_idx = 0 # A
            elif relative_x < 0.62:
                col_idx = 1 # B
            elif relative_x < 0.82:
                col_idx = 2 # C
            else:
                col_idx = 3 # D
            
            if col_idx >= 0:
                row_bubbles[col_idx] = c
                
                # Check fill level
                mask = np.zeros(thresh.shape, dtype="uint8")
                cv2.drawContours(mask, [c], -1, 255, -1)
                mask = cv2.bitwise_and(thresh, thresh, mask=mask)
                total = cv2.countNonZero(mask)
                
                # Calculate fill ratio relative to bubble area
                bubble_area = cv2.contourArea(c)
                fill_ratio = total / float(bubble_area) if bubble_area > 0 else 0
                
                # Minimum threshold to count as a mark (15%)
                if fill_ratio > 0.15 and fill_ratio > max_fill_ratio:
                    max_fill_ratio = fill_ratio
                    bubbled_col = col_idx

        selected_option = options_map.get(bubbled_col)
        if question_number in answer_key:
            selected_answers[question_number] = selected_option
            if selected_option == answer_key[question_number]:
                score += 1
        
        row_data_for_marking.append((q, row_bubbles))
                
    return score, selected_answers, row_data_for_marking, options_map

def mark_answers_on_image(warped, row_data, selected_answers, answer_key, options_map):
    rev_map = {'A': 0, 'B': 1, 'C': 2, 'D': 3}
    
    for (q, row_bubbles) in row_data:
        question_number = q + 1
        if question_number in answer_key:
            correct_opt = answer_key[question_number]
            correct_idx = rev_map.get(correct_opt)
            selected_opt = selected_answers.get(question_number)
            selected_idx = rev_map.get(selected_opt)
            
            # Draw correct answer outline (Blue)
            if correct_idx is not None and row_bubbles[correct_idx] is not None:
                cv2.drawContours(warped, [row_bubbles[correct_idx]], -1, (255, 0, 0), 2)
            
            # Draw student's answer (Green if right, Red if wrong)
            if selected_idx is not None and row_bubbles[selected_idx] is not None:
                color = (0, 255, 0) if selected_opt == correct_opt else (0, 0, 255)
                cv2.drawContours(warped, [row_bubbles[selected_idx]], -1, color, 3)
                    
    return warped

def process_omr(image_path, answer_key, output_path):
    image = load_and_resize_image(image_path)
    gray_contours, gray_bubbles, edged = preprocess_image(image)
    
    # Find all potential 4-pointed boxes on the sheet
    cnts = cv2.findContours(edged.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cnts = imutils.grab_contours(cnts)
    
    docCnts = []
    if len(cnts) > 0:
        cnts = sorted(cnts, key=cv2.contourArea, reverse=True)
        for c in cnts:
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.02 * peri, True)
            if len(approx) == 4: docCnts.append(approx)

    # Check the whole image as a fallback
    docCnts.append(None)

    best_warped = image
    best_warped_gray = gray_bubbles
    max_bubble_count = 0
    best_rows = []

    # Iterate through potential boxes and pick the one with the most "answer rows"
    for docCnt in docCnts[:6]: # Check top 6 largest boxes
        if docCnt is None:
            warped = image.copy()
            warped_gray = gray_bubbles.copy()
        else:
            warped = perspective_transform(image, docCnt)
            warped_gray = perspective_transform(gray_bubbles, docCnt)
        
        thresh = threshold_image(warped_gray)
        rows = detect_bubbles(thresh)
        count = sum(len(r) for r in rows)
        if count > max_bubble_count:
            max_bubble_count = count
            best_warped = warped
            best_warped_gray = warped_gray
            best_rows = rows

    if not best_rows:
        raise ValueError("Could not find the OMR answer grid.")

    final_thresh = threshold_image(best_warped_gray)
    score, selected, row_data, options_map = evaluate_answers(final_thresh, best_rows, answer_key)
    marked_image = mark_answers_on_image(best_warped, row_data, selected, answer_key, options_map)
    cv2.imwrite(output_path, marked_image)
    return score, len(answer_key), selected, output_path

def extract_answers(image_path):
    image = load_and_resize_image(image_path)
    gray_contours, gray_bubbles, edged = preprocess_image(image)
    
    cnts = cv2.findContours(edged.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cnts = imutils.grab_contours(cnts)
    docCnts = [None]
    if len(cnts) > 0:
        cnts = sorted(cnts, key=cv2.contourArea, reverse=True)
        for c in cnts:
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.02 * peri, True)
            if len(approx) == 4: docCnts.insert(0, approx)

    best_rows = []
    best_thresh = None
    max_count = 0

    for docCnt in docCnts[:6]:
        if docCnt is None:
            gray_warp = gray_bubbles.copy()
        else:
            gray_warp = perspective_transform(gray_bubbles, docCnt)
        thresh = threshold_image(gray_warp)
        rows = detect_bubbles(thresh)
        cnt = sum(len(r) for r in rows)
        if cnt > max_count:
            max_count = cnt
            best_rows = rows
            best_thresh = thresh

    if not best_rows: raise ValueError("Could not detect the answer key grid.")
    dummy_key = {i+1: 'X' for i in range(len(best_rows))}
    _, extracted, _, _ = evaluate_answers(best_thresh, best_rows, dummy_key)
    return extracted

def future_ai_enhancement():
    pass
