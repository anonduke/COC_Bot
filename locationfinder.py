from pynput import mouse, keyboard

log_file = "click_coordinates.txt"
stop_key = keyboard.Key.esc  # Press 'Esc' to stop the script

def on_click(x, y, button, pressed):
    if pressed:
        with open(log_file, "a") as f:
            f.write(f"{x}, {y}\n")
        print(f"Logged: {x}, {y}")

def on_press(key):
    if key == stop_key:
        print("Stopping the script...")
        return False  # Stops the keyboard listener and exits the program

# Start mouse listener
mouse_listener = mouse.Listener(on_click=on_click)
mouse_listener.start()

# Start keyboard listener
with keyboard.Listener(on_press=on_press) as keyboard_listener:
    keyboard_listener.join()
