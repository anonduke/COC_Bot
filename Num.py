import cv2
import pytesseract

# Specify the path to the Tesseract executable (required for Windows)
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

def recognize_numbers(image_path):
    # Read the image
    image = cv2.imread(image_path)

    # Convert the image to grayscale for better OCR results
    gray_image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Apply thresholding to improve OCR accuracy
    _, threshold_img = cv2.threshold(gray_image, 150, 255, cv2.THRESH_BINARY)

    # Extract text using pytesseract (digits only)
    custom_config = r'--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789'
    recognized_text = pytesseract.image_to_string(threshold_img, config=custom_config)

    # Clean and display the result
    numbers = ''.join(filter(str.isdigit, recognized_text))
    print(f"Recognized Numbers: {numbers if numbers else 'No numbers found'}")

# Example usage
if __name__ == "__main__":
    image_path = "sample_image.png"  # Change this to your image file
    recognize_numbers(image_path)
