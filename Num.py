import tkinter as tk
import pygetwindow as gw
import time

GAME_TITLE = "LDPlayer"

# Relative offsets (x, y, width, height)
GOLD_OFFSET   = (984, 59, 142, 27)
ELIXIR_OFFSET = (970, 121, 159, 28)


def get_absolute_coords(offset):
    """Convert relative offset to absolute screen coordinates"""
    wins = gw.getWindowsWithTitle(GAME_TITLE)
    if not wins:
        print(f"❌ {GAME_TITLE} window not found!")
        return None
    
    win = wins[0]
    base_x, base_y = win.left, win.top
    
    x = base_x + offset[0]
    y = base_y + offset[1]
    w = offset[2]
    h = offset[3]
    
    return (x, y, w, h)

def highlight_region(region, label="Region"):
    """Highlight a region with green box"""
    if not region:
        return
    
    x, y, w, h = region
    
    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.attributes("-alpha", 0.3)
    root.attributes("-topmost", True)
    root.configure(bg="black")
    
    canvas = tk.Canvas(root, highlightthickness=0, bg="black")
    canvas.pack(fill="both", expand=True)
    
    # Draw green rectangle
    canvas.create_rectangle(x, y, x + w, y + h, outline="green", width=4)
    
    print(f"✅ Highlighting {label}: ({x}, {y}, {w}, {h})")
    print("Press ESC to close")
    
    root.bind("<Escape>", lambda e: root.destroy())
    root.mainloop()

if __name__ == "__main__":
    # Show GOLD region
    gold_region = get_absolute_coords(GOLD_OFFSET)
    if gold_region:
        highlight_region(gold_region, "GOLD")
    
    # Show ELIXIR region
    elixir_region = get_absolute_coords(ELIXIR_OFFSET)
    if elixir_region:
        highlight_region(elixir_region, "ELIXIR")