from flask import Flask, request, redirect, session, render_template
import math
import requests
import urllib.parse
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import cv2
import numpy as np
import os
from urllib.request import urlopen

app = Flask(__name__)
app.secret_key = 'a_secret_key_for_sessions'


# Global dictionary to store credentials
credentials = {
    "client_id": '',
    "client_secret": ''
}
REDIRECT_URI = 'http://localhost:5000/callback'


@app.route('/')
def home():
    return render_template('index.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        credentials['client_id'] = request.form.get('client_id')
        credentials['client_secret'] = request.form.get('client_secret')
        session['client_id'] = credentials['client_id']
        session['client_secret'] = credentials['client_secret']
        auth_url = (
            'https://accounts.spotify.com/authorize?'
            f"response_type=code&client_id={urllib.parse.quote(credentials['client_id'])}"
            f"&scope=user-top-read&redirect_uri={urllib.parse.quote(REDIRECT_URI)}"
            '&show_dialog=true'
        )

        return redirect(auth_url)
    return render_template('login.html', client_id=credentials['client_id'], client_secret=credentials['client_secret'])

@app.route('/callback')
def callback():
    code = request.args.get('code')
    token_url = 'https://accounts.spotify.com/api/token'
    payload = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': REDIRECT_URI,
        'client_id': credentials['client_id'],
        'client_secret': credentials['client_secret']
    }
    response = requests.post(token_url, data=payload)
    access_token = response.json().get('access_token')
    session['access_token'] = access_token
    return redirect('/choose')


@app.route('/choose')
def choose():
    return render_template('choose.html')

@app.route('/display_stats', methods=['POST'])
def display_stats():
    stat_type = request.form.get('stat_type')
    access_token = session.get('access_token')
    if not access_token:
        return redirect('/login')
    headers = {'Authorization': f'Bearer {access_token}'}

    # Initialize the list for image URLs
    image_urls = []
    items = []
    album_ids_seen = set()

    if stat_type in ['albums', 'albums + songs']:
        # Fetch top tracks to extract albums
        url = 'https://api.spotify.com/v1/me/top/tracks?time_range=medium_term&limit=50'
        page_count = 0
        while url and page_count < 60:
            response = requests.get(url, headers=headers)
            if response.status_code != 200:
                print("Failed to fetch data:", response.text)
                return "Failed to fetch data from Spotify", response.status_code

            data = response.json()
            for track in data.get('items', []):
                album = track['album']
                album_id = album['id']
                album_type = album['album_type']  # Get the album type
                if stat_type == 'albums + songs':
                    albumType2 = album_type
                else:
                    albumType2 = 'ALBUM'
                # Filter by album type; include only actual albums (exclude singles and compilations if desired)
                if album_id not in album_ids_seen and album['images'] and album_type == albumType2:
                    album_ids_seen.add(album_id)  # Mark this album as seen
                    image_urls.append(album['images'][0]['url'])
            url = data.get('next')  # Prepare URL for the next page, if any
            page_count += 1
    if image_urls:  # Ensure there's at least one URL to process
        collage, score = create_collage(image_urls)
        collage = collage.resize((579, 591))
        # Specify the path to your pre-made background image
        background_image_path = os.path.join(app.root_path, 'static', 'template.png')
        # Specify the position to overlay the collage on the background image
        overlay_position = (445, 269)  # Adjust this based on your background image and preferences
        
        # Call the overlay function to combine the collage with the pre-made background
        spotify_username = get_spotify_username(session.get('access_token'))
        if spotify_username is None:
            spotify_username = "Spotify User"  # Fallback text
        
        # Call the overlay function to combine the collage with the pre-made background, now including the text
        final_image = overlay_collage_on_background(
            collage, 
            background_image_path, 
            overlay_position,
            text=spotify_username,  # Add the Spotify username as text
            font_path= os.path.join(app.root_path, 'static', 'CircularSpotifyText-Black.otf'),
            score=score
        )
        
        # Specify the filepath where you want to save the final image
        filepath = f'static/collages/Colorify_Collage.png'
        # Save the final image to the specified path
        final_image.save(filepath)
        
        # Your existing code to return or display the image
        return render_template('show_collage.html', collage_path=filepath)
    else:
        # Handle the case where no image URLs were fetched or an error occurred
        return "Failed to fetch images or no images available", 400


def get_spotify_username(access_token):
    """Get the Spotify username of the current user."""
    url = 'https://api.spotify.com/v1/me'
    headers = {'Authorization': f'Bearer {access_token}'}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json().get('display_name')  # Or 'id' depending on what you want to show
    else:
        return None

def fit_text_to_box(text, font_path, box_width, box_height, max_font_size=160):
    """
    Adjusts the font size so that the given text fits within the specified box dimensions.

    Parameters:
    - text: The text to be drawn.
    - font_path: The path to the font file.
    - box_width, box_height: The dimensions of the bounding box.
    - max_font_size: Starting maximum font size.

    Returns:
    - A tuple containing the adjusted font size and an ImageFont instance.
    """
    font_size = max_font_size
    font = ImageFont.truetype(font_path, font_size)

    # Create a dummy image just for measuring text size
    dummy_image = Image.new('RGB', (100, 100))
    draw = ImageDraw.Draw(dummy_image)

    text_width, text_height = draw.textsize(text, font=font)
    
    # Decrease font size until the text fits within the specified dimensions
    while text_width > box_width or text_height > box_height and font_size > 1:
        font_size -= 1
        font = ImageFont.truetype(font_path, font_size)
        text_width, text_height = draw.textsize(text, font=font)

    return font_size, font

def has_enough_red(image_url, threshold=0.3):
    """
    Check if the given image URL has a sufficient amount of true red, now adjusted to also detect slightly lighter reds while still excluding light reds like pink, as well as purples, magenta, and light oranges, using OpenCV.
    
    :param image_url: URL of the image to be checked.
    :param threshold: A float representing the percentage of the image that must be true red. Increased for a stricter check.
    :return: A boolean indicating whether the image has enough true red.
    """
    # Read the image from URL into a numpy array
    resp = urlopen(image_url)
    image = np.asarray(bytearray(resp.read()), dtype="uint8")
    image = cv2.imdecode(image, cv2.IMREAD_COLOR)
    
    # Convert the image to HSV color space for better color segmentation
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    
    # Adjust ranges to also capture slightly lighter reds
    lower_red1, upper_red1 = np.array([0, 180, 50]), np.array([10, 255, 245])  # Adjusted for lighter reds near 0 hue
    lower_red2, upper_red2 = np.array([170, 180, 50]), np.array([180, 255, 245])  # Adjusted for lighter reds on the upper hue range
    
    # Apply the red color masks
    red_mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
    red_mask2 = cv2.inRange(hsv, lower_red2, upper_red2)
    red_mask = cv2.bitwise_or(red_mask1, red_mask2)
    
    # Calculate if the true red pixels meet the threshold requirement
    red_ratio = np.sum(red_mask > 0) / red_mask.size
    return red_ratio >= threshold


def has_enough_orange(image_url, threshold=0.2):
    """
    Check if the given image URL has a strictly sufficient amount of orange, excluding too dark or too light oranges, using OpenCV.
    
    :param image_url: URL of the image to be checked.
    :param threshold: A float representing the percentage of the image that must be a distinctly vibrant and not too dark or light shade of orange.
    :return: A boolean indicating whether the image has enough of the specific orange shade.
    """
    # Load the image from the URL
    resp = urlopen(image_url)
    image = np.asarray(bytearray(resp.read()), dtype="uint8")
    image = cv2.imdecode(image, cv2.IMREAD_COLOR)
    
    # Convert to HSV color space
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    
    # Adjust the range for orange to be stricter on saturation and value to exclude too light or dark shades
    # Keeping the hue range to target oranges but tightening S and V ranges
    lower_orange = np.array([7, 150, 120])  # Adjust S and V to exclude too light/pale oranges
    upper_orange = np.array([28, 255, 255])  # Adjust S and V to exclude too dark oranges

    # Create a mask for pixels within the stricter orange range
    orange_mask = cv2.inRange(hsv, lower_orange, upper_orange)
    
    # Calculate the proportion of orange pixels
    orange_percentage = np.sum(orange_mask > 0) / orange_mask.size

    # Return True if the orange area exceeds the threshold, indicating the presence of the targeted orange shade
    return orange_percentage >= threshold


def has_enough_yellow(image_url, threshold=0.2):
    """
    Check if the given image URL has a sufficient amount of yellow using OpenCV.
    The hue range has been broadened to capture a wider spectrum of yellow shades.

    :param image_url: URL of the image to be checked.
    :param threshold: A float representing the percentage of the image that must be yellow.
    :return: A boolean indicating whether the image has enough yellow.
    """
    # Load the image from the URL
    resp = urlopen(image_url)
    image = np.asarray(bytearray(resp.read()), dtype="uint8")
    image = cv2.imdecode(image, cv2.IMREAD_COLOR)
    
    # Convert to HSV color space
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    
    # Broadened range for yellow
    # Adjusting the lower bound to include lighter yellows and the upper bound for deeper yellows
    lower_yellow = np.array([20, 90, 90])  # Slightly lower hue, and a bit more lenient on saturation and value
    upper_yellow = np.array([45, 255, 255])  # Extended upper hue to include more golden tones

    # Create a mask for pixels within the yellow range
    yellow_mask = cv2.inRange(hsv, lower_yellow, upper_yellow)
    
    # Calculate the proportion of yellow pixels
    yellow_percentage = np.sum(yellow_mask > 0) / yellow_mask.size

    # Return True if the yellow area exceeds the threshold
    return yellow_percentage >= threshold

def has_enough_orangey_yellow(image_url, threshold=0.15):  # Lowered threshold
    """
    Adjusted for a broader hue range and lower threshold.
    """
    resp = urlopen(image_url)
    image = np.asarray(bytearray(resp.read()), dtype="uint8")
    image = cv2.imdecode(image, cv2.IMREAD_COLOR)
    
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    lower_orangey_yellow = np.array([20, 80, 80])  # Broadened range
    upper_orangey_yellow = np.array([40, 255, 255])

    mask = cv2.inRange(hsv, lower_orangey_yellow, upper_orangey_yellow)
    percentage = np.sum(mask > 0) / mask.size

    return percentage >= threshold

def has_enough_green_yellow(image_url, threshold=0.15):  # Keep the lowered threshold
    """
    Further broadened the hue range and adjusted the saturation and value thresholds
    to be more inclusive of various shades of green-yellow, aiming to detect everything
    from pale lime to deeper greenish-yellow tones.
    """
    resp = urlopen(image_url)
    image = np.asarray(bytearray(resp.read()), dtype="uint8")
    image = cv2.imdecode(image, cv2.IMREAD_COLOR)
    
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    
    # Broaden the range further to include lighter and deeper shades of green-yellow
    # This range adjustment aims to be inclusive of variations from lime green to yellowish-green
    lower_green_yellow = np.array([25, 70, 70])  # Start from a lighter, more yellowish hue
    upper_green_yellow = np.array([65, 255, 255])  # Extend to capture more of the green spectrum

    mask = cv2.inRange(hsv, lower_green_yellow, upper_green_yellow)
    percentage = np.sum(mask > 0) / mask.size

    return percentage >= threshold

def has_enough_green(image_url, threshold=0.2):
    """
    Check if the given image URL has a leniently sufficient amount of green, including a broad spectrum of green shades from light to dark, and from vibrant to more subdued, using OpenCV.
    
    :param image_url: URL of the image to be checked.
    :param threshold: A float representing the percentage of the image that must be covered by any shade of green.
    :return: A boolean indicating whether the image has a broad spectrum of green shades.
    """
    # Load the image from the URL
    resp = urlopen(image_url)
    image = np.asarray(bytearray(resp.read()), dtype="uint8")
    image = cv2.imdecode(image, cv2.IMREAD_COLOR)
    
    # Convert to HSV color space for better color segmentation
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    
    # Adjust the range for green to be more lenient, including a wider variety of greens
    # Expand the hue range to include both yellow-green and blue-green shades
    lower_green = np.array([35, 40, 40])  # Lower values to include lighter and less saturated greens
    upper_green = np.array([85, 255, 255])  # Higher values to also include darker greens

    # Create a mask for pixels within the expanded green range
    green_mask = cv2.inRange(hsv, lower_green, upper_green)
    
    # Calculate the proportion of green pixels
    green_percentage = np.sum(green_mask > 0) / green_mask.size

    # Return True if the green area exceeds the threshold, indicating a lenient detection of green
    return green_percentage >= threshold

def has_enough_blue(image_url, threshold=0.15):
    """
    Check if the given image URL has a sufficient amount of blue using OpenCV.
    Targets a broad range of blue shades.

    :param image_url: URL of the image to be checked.
    :param threshold: A float representing the percentage of the image that must be blue.
    :return: A boolean indicating whether the image has enough blue.
    """
    resp = urlopen(image_url)
    image = np.asarray(bytearray(resp.read()), dtype="uint8")
    image = cv2.imdecode(image, cv2.IMREAD_COLOR)
    
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    lower_blue = np.array([90, 50, 50])
    upper_blue = np.array([128, 255, 255])
    
    blue_mask = cv2.inRange(hsv, lower_blue, upper_blue)
    blue_percentage = np.sum(blue_mask > 0) / blue_mask.size

    return blue_percentage >= threshold

def has_enough_magenta(image_url, threshold=0.15):
    """
    Check if the given image URL has a sufficient amount of magenta using OpenCV.
    Targets magenta hues by combining ranges from both ends of the HSV hue spectrum.

    :param image_url: URL of the image to be checked.
    :param threshold: A float representing the percentage of the image that must be magenta.
    :return: A boolean indicating whether the image has enough magenta.
    """
    resp = urlopen(image_url)
    image = np.asarray(bytearray(resp.read()), dtype="uint8")
    image = cv2.imdecode(image, cv2.IMREAD_COLOR)
    
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    # Magenta at the higher end
    lower_magenta_high = np.array([129, 50, 50])
    upper_magenta_high = np.array([180, 255, 255])
    # Magenta at the lower end
    lower_magenta_low = np.array([0, 50, 50])
    upper_magenta_low = np.array([10, 255, 255])
    
    magenta_mask_high = cv2.inRange(hsv, lower_magenta_high, upper_magenta_high)
    magenta_mask_low = cv2.inRange(hsv, lower_magenta_low, upper_magenta_low)
    # Combine masks
    magenta_mask = magenta_mask_high + magenta_mask_low
    magenta_percentage = np.sum(magenta_mask > 0) / magenta_mask.size

    return magenta_percentage >= threshold

def overlay_collage_on_background(collage, background_image_path, overlay_position=(0, 0), text="", font_path="/static/CircularSpotifyText-Black.otf", score=0):
    # Load the pre-made background image
    background_image = Image.open(background_image_path).convert('RGB')
    
    collage = collage.convert('RGB')
    # Calculate the position to overlay the collage on the background
    # overlay_position is a tuple (x, y) specifying where the top-left
    # corner of the collage should be placed on the background
    position = overlay_position
    
    # Place the collage on the background image
    background_image.paste(collage, position)
    
    # Now, draw the text
    draw = ImageDraw.Draw(background_image)
    # Load your custom font (ensure the path is correct)
    font_size = 160  # Adjust as needed
    font = ImageFont.truetype(font_path, font_size)
    text_width, text_height = draw.textsize(text, font=font)
    text_y = 220 - text_height
    adjusted_font_size, adjusted_font = fit_text_to_box(text, font_path, 906, 125)
    # Define text position (adjust as needed, example places it at bottom right of collage)
    text_position = (71,text_y)
    draw.text(text_position, text, fill="white", font=adjusted_font) 
    print("Drawing text:", text, "at", text_position, "with font size", font_size)
    font = ImageFont.truetype(font_path, 48)
    text_width, text_height = draw.textsize(str(score), font=font)
    text_x = 195-text_width
    text_y = 582-text_height
    text_position = (text_x,text_y)
    draw.text(text_position, str(score), fill="white", font=font) 
    # Save or display the final image
    # For example, save the image:
    final_image_path = 'static/collages/Colorify_Collage.png'
    background_image.save(final_image_path)
    
    # Or return the final image object if you want to further process or directly display it
    return background_image



def create_collage(image_urls, collage_width=4, collage_height=4, red_positions=[(0, 0), (0, 1), (1, 0)], orange_positions=[(2, 0), (1, 1), (0, 2)], yellow_positions=[(2, 1), (1, 2)], orangey_yellow_positions=[(0, 3)], green_yellow_positions=[(3, 0)], green_positions = [(1, 3), (2, 2), (3,1)], blue_positions = [(2, 3), (3, 2)], magenta_positions = [(3, 3)],  red_threshold=0.5, orange_threshold=0.5, yellow_threshold=0.50, orangey_yellow_threshold=0.40, green_yellow_threshold=0.30, green_threshold=0.50, blue_threshold=0.50, magenta_threshold=0.50):
    thumbnail_size = (160, 160)  # Size of each thumbnail in the collage
    new_im = Image.new('RGB', (thumbnail_size[0] * collage_width, thumbnail_size[1] * collage_height))
    
    red_images = []
    orange_images = []
    yellow_images = []
    orangey_yellow_images = []
    green_yellow_images = []
    other_images = []
    green_images = []
    blue_images = []
    magenta_images = []

    # Counters for found red, orange, and yellow images
    score = 0
    red_count, orange_count, yellow_count, orangey_yellow_count, green_yellow_count, green_count, blue_count, magenta_count = 0, 0, 0, 0, 0, 0, 0, 0
    max_red_orange_yellow = 3  # Maximum number of red and orange images to find, 2 for yellow

    for index, url in enumerate(image_urls):
        if red_count < max_red_orange_yellow and has_enough_red(url, red_threshold):
            red_images.append(url)
            print(f"red image detected: {url}")
            red_count += 1
        elif orange_count < max_red_orange_yellow and has_enough_orange(url, orange_threshold):
            orange_images.append(url)
            orange_count += 1
        elif yellow_count < 2 and has_enough_yellow(url, yellow_threshold):  # Check for yellow images
            yellow_images.append(url)
            yellow_count += 1
        elif orangey_yellow_count < 1 and has_enough_orangey_yellow(url, orangey_yellow_threshold):  # Check for yellow images
            orangey_yellow_images.append(url)
            print(f"Orangey_Yellow image detected: {url}")
            orangey_yellow_count += 1
        elif green_yellow_count < 1 and has_enough_green_yellow(url, green_yellow_threshold):  # Check for yellow images
            green_yellow_images.append(url)
            print(f"Green_Yellow image detected: {url}")
            green_yellow_count += 1
        elif green_count < 3 and has_enough_green(url, green_threshold):  # Adjust the threshold as needed
            green_images.append(url)
            green_count += 1
        elif blue_count < 2 and has_enough_blue(url, 0.15):
            blue_images.append(url)
            blue_count += 1
        elif magenta_count < 1 and has_enough_magenta(url, 0.15):
            magenta_images.append(url)
            magenta_count += 1
        if red_count >= max_red_orange_yellow and orange_count >= max_red_orange_yellow and yellow_count >= 2 and orangey_yellow_count >= 1 and green_yellow_count >= 1 and green_count >= 3 and blue_count >= 2 and magenta_count >= 1:
            score = math.ceil((1 - (index/1000))*100)
            if score < 0:
                score = 0
            print(f"{score}%")
            break  # Stop the loop once we have enough of red, orange, and yellow images


    # Function to place an image in the collage
    def place_image(url, position):
        image = Image.open(BytesIO(requests.get(url).content))
        image.thumbnail(thumbnail_size)
        x = position[0] * thumbnail_size[0]
        y = position[1] * thumbnail_size[1]
        new_im.paste(image, (x, y))
    
    # Place red and orange images in their positions
    for pos in red_positions:
        if red_images:
            place_image(red_images.pop(0), pos)
    for pos in orange_positions:
        if orange_images:
            place_image(orange_images.pop(0), pos)
    for pos in yellow_positions:
        if yellow_images:
            place_image(yellow_images.pop(0), pos)
    for pos in orangey_yellow_positions:
        if orangey_yellow_images:
            place_image(orangey_yellow_images.pop(0), pos)
    for pos in green_yellow_positions:
        if green_yellow_images:
            place_image(green_yellow_images.pop(0), pos)
    for pos in green_positions:
        if green_images:
            place_image(green_images.pop(0), pos)
    for pos in blue_positions:
        if blue_images:
            place_image(blue_images.pop(0), pos)
    for pos in magenta_positions:
        if magenta_images:
            place_image(magenta_images.pop(0), pos)


    # Fill the rest of the positions with other images
    all_positions = [(x, y) for y in range(collage_height) for x in range(collage_width)]
    remaining_positions = [pos for pos in all_positions if pos not in red_positions and pos not in orange_positions and pos not in yellow_positions and pos not in orangey_yellow_positions and pos not in green_yellow_positions and pos not in green_positions and pos not in blue_positions and pos not in magenta_positions]

    for pos in remaining_positions:
        if other_images:
            place_image(other_images.pop(0), pos)
        elif red_images:  # Use any remaining red images if there are no more other images
            place_image(red_images.pop(0), pos)
        elif orange_images:  # Lastly, use any remaining orange images
            place_image(orange_images.pop(0), pos)

    return new_im, score



if __name__ == '__main__':
    app.run(debug=True)