import json
import math
from collections import Counter
from datetime import datetime
import logging
from logging.handlers import TimedRotatingFileHandler
from flask import Flask, request, render_template, redirect, url_for, jsonify, flash
import time
from geopy.exc import GeocoderTimedOut
from key import key, mapId
from geopy.geocoders import Nominatim
from dotenv import load_dotenv
import os
from models import db, CustomJSONEncoder, IntervalOne, IntervalTwo, \
    IntervalThree, IntervalFour, IntervalFive, IntervalSix, FilteredModel, GeocodeCache
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor
import requests_cache
from utils.generate_grids import create_histogram_grid, create_grid_heatmap, create_polygon

# check if the log directory exists.
log_dir = os.path.join(os.path.dirname(__file__),'logs')
os.makedirs(log_dir,exist_ok=True)


load_dotenv()

app = Flask(__name__)
app.json_encoder = CustomJSONEncoder
app.secret_key = 'your_secret_key'

app.config['MONGODB_SETTINGS'] = {
    'db': 'sample_geospatial',
    'host': os.getenv('MONGODB_URI'),
}
db.init_app(app)

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# Configure logging
log_filename=os.path.join(log_dir,'crimedata_app.log')
handler = TimedRotatingFileHandler(log_filename,when='midnight', interval=1, backupCount=2)
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))

logger = logging.getLogger("crimedata_logger")
logger.setLevel(logging.INFO)
logger.addHandler(handler)

#
# # Log messages
logger.info("This is an info message")
logger.warning("This is a warning")
logger.error("This is an error")


class UserData:
    latitude = None
    longitude = None
    interval = None
    radius = 0
    units = None
    grid = None

    def __init__(self):
        self.safecoordinates = []
        self.workcoordinates = []
        self.currentcoordinates = []
        self.destinationcoordinates = []

    # Convert radius and units (meters) to distance.
    # Create geometry to see if points fall within the radius (safe,current,work)

    def add_safe_coordinates(self, latitude, longitude):
        self.safecoordinates.append(longitude)
        self.safecoordinates.append(latitude)

    def add_work_coordinates(self, latitude, longitude):
        self.workcoordinates.append(longitude)
        self.workcoordinates.append(latitude)

    def add_current_coordinates(self, latitude, longitude):
        self.currentcoordinates.append(longitude)
        self.currentcoordinates.append(latitude)

    def add_destination_coordinates(self, latitude, longitude):
        self.destinationcoordinates.append(longitude)
        self.destinationcoordinates.append(latitude)

INTERVAL_GRID_MAPPING  = {
    "12AM-4AM": IntervalOne,
    "4AM-8AM": IntervalTwo,
    "8AM-12PM": IntervalThree,
    "12PM-4PM": IntervalFour,
    "4PM-8PM": IntervalFive,
    "8PM-12AM": IntervalSix
}

def get_meters(radius, unit):
    radius = int(radius)
    if unit == "meters":
        return radius
    elif unit == "mile":
        return radius * 1609.34
    else:
        return radius * 1000


def create_dataframe(rowlist, collist, countlist, centerlist):
    data = {'rows': rowlist, 'cols': collist, 'countlist': countlist, 'centerlist': centerlist}
    return data

def get_middle_element_of_count_list(count_list):
    if len(count_list) % 2 == 0:
        return (len(count_list) // 2) - 1
    else:
        return (len(count_list) - 1) // 2

def extract_coordinates(geojson):
    coordinates_list = []
    feature = geojson['features']

    for f in feature:
        geometry = f['geometry']
        coordinates = geometry['coordinates']
        for inner_list in coordinates:
            for item in inner_list:
                coordinates_list.append(item)

    return coordinates_list

def add_bounds_to_gdf(grid_gdf):
    # Extract bounds: minx (west), miny (south), maxx (east), maxy (north)
    bounds = grid_gdf.geometry.bounds
    grid_gdf["west"] = bounds["minx"]
    grid_gdf["south"] = bounds["miny"]
    grid_gdf["east"] = bounds["maxx"]
    grid_gdf["north"] = bounds["maxy"]
    return grid_gdf

'''Heatmap specific functions'''
def get_count_of_grid_heatmap(polygon_dict, interval):
    # print("Calling get_count_of_grid_heatmap")
    start_time = time.time()
    sublists = [polygon_dict[i:i + 5] for i in range(0, len(polygon_dict), 5)]

    # Define a helper function to process each sublist

    def process_sublist(sublist):
        return search_within_polygon_heatmap(sublist, interval)

    # Use ThreadPoolExecutor for parallel processing
    with ThreadPoolExecutor() as executor:
        count_list = list(executor.map(process_sublist, sublists))

    # count_list = [search_within_polygon_heatmap(sublist, interval) for sublist in sublists]

    logger.info("---  get_count_of_grid_heatmap %s seconds ---" , time.time() - start_time)
    return count_list

def search_within_polygon_heatmap(sublistelement, interval):
    polygon_pipeline = [{
        "$match": {
            "point": {
                "$geoWithin": {
                    "$geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            sublistelement
                        ]
                    }
                }
            }
        }
    }, {
        "$project": {
            "INCIDENT_NO": 1,
            "OFFENSE": 1,
            "point": 1
        }
    }]

    #All returns FilteredModel as the default value
    model = INTERVAL_GRID_MAPPING.get(interval, FilteredModel)
    result = model.objects.aggregate(*polygon_pipeline)
    polygon_result_list = list(result)
    return len(polygon_result_list)

def create_heatmap_polygon(distance, latitude, longitude):
    start_time = time.time()
    grid = create_grid_heatmap(distance, latitude, longitude)
    grid_geojson = grid.to_json()
    grid_geojson_parsed = json.loads(grid_geojson)
    polygon = extract_coordinates(grid_geojson_parsed)
    logger.info("--- create_heatmap_polygon time taken: %s seconds ---", time.time() - start_time)
    return polygon

#area chart functions
def search_within_polygon(sublistelement, interval):
    polygon_pipeline = [{
        "$match": {
            "point": {
                "$geoWithin": {
                    "$geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            sublistelement
                        ]
                    }
                }
            }
        }
    }, {
        "$sort": {"MID_DATE": 1}
    }]

    #All returns FilteredModel as the default value
    model = INTERVAL_GRID_MAPPING.get(interval, FilteredModel)
    result = model.objects.aggregate(*polygon_pipeline)
    polygon_result_list = list(result)
    return polygon_result_list


def get_count_of_polygon(polygon_dict, interval):
    start_time = time.time()
    polygon_objects = search_within_polygon(polygon_dict, interval)
    mid_date_list = [element["MID_DATE"] for element in polygon_objects]
    # print("mid_date_list", mid_date_list)
    years = [datetime.strptime(date, "%m/%d/%Y %I:%M:%S %p").year for date in mid_date_list]
    # print(years)
    # Count occurrences per year
    year_counts = dict(Counter(years))

    # Sort years for plotting
    sorted_years = sorted(year_counts.keys())
    # print("sorted_years",sorted_years)
    sorted_counts = [year_counts[year] for year in sorted_years]
    # print("sorted_counts", sorted_counts)
    df = pd.DataFrame({'Year': sorted_years, 'Count': sorted_counts})
    # group objects by year (sum objects by year)
    # Count crimes by time interval
    # map to a dataframe
    # x axis: year y axis: count
    logger.info("---  get_count_of_polygon %s seconds ---", time.time() - start_time)
    return df


def create_box_polygon(distance, latitude, longitude):
    start_time = time.time()
    box_polygon = create_polygon(distance, latitude, longitude)
    box_geojson = box_polygon.to_json()
    box_geojson_parsed = json.loads(box_geojson)
    polygon = extract_coordinates(box_geojson_parsed)
    logger.info("--- create_box_polygon %s seconds ---", time.time() - start_time)
    return polygon

#  latitude = 1/111320
#  longitude = 111320 * cos(latitude in radians)
def create_bounding_box(latitude, longitude, distance):
    deg_per_meter_lat = 1 / 111320
    lat_diff = distance * deg_per_meter_lat

    deg_per_meter_lon = 1 / (111320 * math.cos(math.radians(latitude)))
    lon_diff = distance * deg_per_meter_lon

    return {
        "west": longitude - lon_diff,
        "east": longitude + lon_diff,
        "south": latitude - lat_diff,
        "north": latitude + lat_diff
    }

def get_data(response):
    json_str = response.get_data(as_text=True)
    python_dict = json.loads(json_str)
    print("python_dict", python_dict)
    # flattened list
    data_not_none_list = [d for sublist in python_dict for d in (sublist if isinstance(sublist, list) else [sublist]) if
                          d != 0]
    data_none_list = [element for element in python_dict if element == 0]
    print("data_not_none_list", data_not_none_list)
    return [data_not_none_list, data_none_list]

def get_count_of_grid_dial(polygon_dict, interval):
    start_time = time.time()
    sublists = [polygon_dict[i:i + 5] for i in range(0, len(polygon_dict), 5)]

    def process_sublist(sublist):
        return search_within_polygon_dial(sublist, interval)

    # Use ThreadPoolExecutor for parallel processing
    with ThreadPoolExecutor() as executor:
        polygon_list = list(executor.map(process_sublist, sublists))
    # print("polygon_list", polygon_list)
    print("--- %s get_count_of_grid_dial secconds ---" % (time.time() - start_time))
    return polygon_list


def search_within_polygon_dial(sublistelement, interval):
    polygon_pipeline = [{
        "$match": {
            "point": {
                "$geoWithin": {
                    "$geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            sublistelement
                        ]
                    }
                }
            }
        }
    } ]

    polygon_pipeline.append({
        "$project": {
            "INCIDENT_NO": 1,
            "OFFENSE": 1,
            "point": 1
        }
    })

    #All returns FilteredModel as the default value
    model = INTERVAL_GRID_MAPPING.get(interval, FilteredModel)
    result = model.objects.aggregate(*polygon_pipeline)
    polygon_result_list = list(result)
    return len(polygon_result_list)

# write data_none_list + data_not_none_list to a file
def create_dial_json(data_not_none_list, data_none_list, filename):
    dial_list = data_none_list + data_not_none_list
    dialjson = {
        "diallist": dial_list
    }

    dial_file_name = os.path.join(PROJECT_DIR, "static", "data", "dial", filename + ".json")
    if dialjson:
        with open(dial_file_name, 'w') as file:
            json.dump(dialjson, file, indent=4)
    else:
        print("dial data not created")

#create a filename value with the grid size and interval as the key
#read the file path based on key and get the dialist output
def read_dial_grid(grid, interval):
    PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
    base_path = os.path.join(PROJECT_DIR, 'static', 'data', 'dial')

    file_mapping = {
        ("500 meters", "12AM-11PM"): "500metersAll.json",
        ("500 meters", "12AM-4AM"): "500meters12AM-4AM.json",
        ("500 meters", "4AM-8AM"): "500meters4AM-8AM.json",
        ("500 meters", "8AM-12PM"): "500meters8AM-12PM.json",
        ("500 meters", "12PM-4PM"): "500meters12PM-4PM.json",
        ("500 meters", "4PM-8PM"): "500meters4PM-8PM.json",
        ("500 meters", "8PM-12AM"): "500meters8PM-12AM.json",

        ("550 meters", "12AM-11PM"): "550metersAll.json",
        ("550 meters", "12AM-4AM"): "550meters12AM-4AM.json",
        ("550 meters", "4AM-8AM"): "550meters4AM-8AM.json",
        ("550 meters", "8AM-12PM"): "550meters8AM-12PM.json",
        ("550 meters", "12PM-4PM"): "550meters12PM-4PM.json",
        ("550 meters", "4PM-8PM"): "550meters4PM-8PM.json",
        ("550 meters", "8PM-12AM"): "550meters8PM-12AM.json",

        ("600 meters", "12AM-11PM"): "600metersAll.json",
        ("600 meters", "12AM-4AM"): "600meters12AM-4AM.json",
        ("600 meters", "4AM-8AM"): "600meters4AM-8AM.json",
        ("600 meters", "8AM-12PM"): "600meters8AM-12PM.json",
        ("600 meters", "12PM-4PM"): "600meters12PM-4PM.json",
        ("600 meters", "4PM-8PM"): "600meters4PM-8PM.json",
        ("600 meters", "8PM-12AM"): "600meters8PM-12AM.json",

        ("650 meters", "12AM-11PM"): "650metersAll.json",
        ("650 meters", "12AM-4AM"): "650meters12AM-4AM.json",
        ("650 meters", "4AM-8AM"): "650meters4AM-8AM.json",
        ("650 meters", "8AM-12PM"): "650meters8AM-12PM.json",
        ("650 meters", "12PM-4PM"): "650meters12PM-4PM.json",
        ("650 meters", "4PM-8PM"): "650meters4PM-8PM.json",
        ("650 meters", "8PM-12AM"): "650meters8PM-12AM.json",

        ("700 meters", "12AM-11PM"): "700metersAll.json",
        ("700 meters", "12AM-4AM"): "700meters12AM-4AM.json",
        ("700 meters", "4AM-8AM"): "700meters4AM-8AM.json",
        ("700 meters", "8AM-12PM"): "700meters8AM-12PM.json",
        ("700 meters", "12PM-4PM"): "700meters12PM-4PM.json",
        ("700 meters", "4PM-8PM"): "700meters4PM-8PM.json",
        ("700 meters", "8PM-12AM"): "700meters8PM-12AM.json",

        ("750 meters", "12AM-11PM"): "750metersAll.json",
        ("750 meters", "12AM-4AM"): "750meters12AM-4AM.json",
        ("750 meters", "4AM-8AM"): "750meters4AM-8AM.json",
        ("750 meters", "8AM-12PM"): "750meters8AM-12PM.json",
        ("750 meters", "12PM-4PM"): "750meters12PM-4PM.json",
        ("750 meters", "4PM-8PM"): "750meters4PM-8PM.json",
        ("750 meters", "8PM-12AM"): "750meters8PM-12AM.json",

        ("800 meters", "12AM-11PM"): "800metersAll.json",
        ("800 meters", "12AM-4AM"): "800meters12AM-4AM.json",
        ("800 meters", "4AM-8AM"): "800meters4AM-8AM.json",
        ("800 meters", "8AM-12PM"): "800meters8AM-12PM.json",
        ("800 meters", "12PM-4PM"): "800meters12PM-4PM.json",
        ("800 meters", "4PM-8PM"): "800meters4PM-8PM.json",
        ("800 meters", "8PM-12AM"): "800meters8PM-12AM.json",

        ("850 meters", "12AM-11PM"): "850metersAll.json",
        ("850 meters", "12AM-4AM"): "850meters12AM-4AM.json",
        ("850 meters", "4AM-8AM"): "850meters4AM-8AM.json",
        ("850 meters", "8AM-12PM"): "850meters8AM-12PM.json",
        ("850 meters", "12PM-4PM"): "850meters12PM-4PM.json",
        ("850 meters", "4PM-8PM"): "850meters4PM-8PM.json",
        ("850 meters", "8PM-12AM"): "850meters8PM-12AM.json",

        ("900 meters", "12AM-11PM"): "900metersAll.json",
        ("900 meters", "12AM-4AM"): "900meters12AM-4AM.json",
        ("900 meters", "4AM-8AM"): "900meters4AM-8AM.json",
        ("900 meters", "8AM-12PM"): "900meters8AM-12PM.json",
        ("900 meters", "12PM-4PM"): "900meters12PM-4PM.json",
        ("900 meters", "4PM-8PM"): "900meters4PM-8PM.json",
        ("900 meters", "8PM-12AM"): "900meters8PM-12AM.json",

        ("950 meters", "12AM-11PM"): "950metersAll.json",
        ("950 meters", "12AM-4AM"): "950meters12AM-4AM.json",
        ("950 meters", "4AM-8AM"): "950meters4AM-8AM.json",
        ("950 meters", "8AM-12PM"): "950meters8AM-12PM.json",
        ("950 meters", "12PM-4PM"): "950meters12PM-4PM.json",
        ("950 meters", "4PM-8PM"): "950meters4PM-8PM.json",
        ("950 meters", "8PM-12AM"): "950meters8PM-12AM.json",

        ("1 kilometer", "12AM-11PM"): "1kilometerAll.json",
        ("1 kilometer", "12AM-4AM"): "1kilometer12AM-4AM.json",
        ("1 kilometer", "4AM-8AM"): "1kilometer4AM-8AM.json",
        ("1 kilometer", "8AM-12PM"): "1kilometer8AM-12PM.json",
        ("1 kilometer", "12PM-4PM"): "1kilometer12PM-4PM.json",
        ("1 kilometer", "4PM-8PM"): "1kilometer4PM-8PM.json",
        ("1 kilometer", "8PM-12AM"): "1kilometer8PM-12AM.json",

        ("1 mile", "12AM-11PM"): "1mileAll.json",
        ("1 mile", "12AM-4AM"): "1mile12AM-4AM.json",
        ("1 mile", "4AM-8AM"): "1mile4AM-8AM.json",
        ("1 mile", "8AM-12PM"): "1mile8AM-12PM.json",
        ("1 mile", "12PM-4PM"): "1mile12PM-4PM.json",
        ("1 mile", "4PM-8PM"): "1mile4PM-8PM.json",
        ("1 mile", "8PM-12AM"): "1mile8PM-12AM.json",
    }
    file_name = file_mapping.get((grid, interval))
    if not file_name:
        raise ValueError(f"No file mapping found for grid: {grid}, interval: {interval}")
    file_path = os.path.join(base_path, file_name)
    with open(file_path, 'r') as file:
        data = json.load(file)
        appended_list = data.get("diallist", [])
    return appended_list

@app.route('/createnewgrids')
def create_new_grids():
    create_grids((500, "meters", "All"))
    create_grids((500, "meters", "12AM-4AM"))
    create_grids((500, "meters", "4AM-8AM"))
    create_grids((500, "meters", "8AM-12PM"))
    create_grids((500, "meters", "12PM-4PM"))
    create_grids((500, "meters", "4PM-8PM"))
    create_grids((500, "meters", "8PM-12AM"))

    create_grids((550, "meters", "All"))
    create_grids((550, "meters", "12AM-4AM"))
    create_grids((550, "meters", "4AM-8AM"))
    create_grids((550, "meters", "8AM-12PM"))
    create_grids((550, "meters", "12PM-4PM"))
    create_grids((550, "meters", "4PM-8PM"))
    create_grids((550, "meters", "8PM-12AM"))

    create_grids((600, "meters", "All"))
    create_grids((600, "meters", "12AM-4AM"))
    create_grids((600, "meters", "4AM-8AM"))
    create_grids((600, "meters", "8AM-12PM"))
    create_grids((600, "meters", "12PM-4PM"))
    create_grids((600, "meters", "4PM-8PM"))
    create_grids((600, "meters", "8PM-12AM"))

    create_grids((650, "meters", "All"))
    create_grids((650, "meters", "12AM-4AM"))
    create_grids((650, "meters", "4AM-8AM"))
    create_grids((650, "meters", "8AM-12PM"))
    create_grids((650, "meters", "12PM-4PM"))
    create_grids((650, "meters", "4PM-8PM"))
    create_grids((650, "meters", "8PM-12AM"))

    create_grids((700, "meters", "All"))
    create_grids((700, "meters", "12AM-4AM"))
    create_grids((700, "meters", "4AM-8AM"))
    create_grids((700, "meters", "8AM-12PM"))
    create_grids((700, "meters", "12PM-4PM"))
    create_grids((700, "meters", "4PM-8PM"))
    create_grids((700, "meters", "8PM-12AM"))

    create_grids((750, "meters", "All"))
    create_grids((750, "meters", "12AM-4AM"))
    create_grids((750, "meters", "4AM-8AM"))
    create_grids((750, "meters", "8AM-12PM"))
    create_grids((750, "meters", "12PM-4PM"))
    create_grids((750, "meters", "4PM-8PM"))
    create_grids((750, "meters", "8PM-12AM"))

    create_grids((800, "meters", "All"))
    create_grids((800, "meters", "12AM-4AM"))
    create_grids((800, "meters", "4AM-8AM"))
    create_grids((800, "meters", "8AM-12PM"))
    create_grids((800, "meters", "12PM-4PM"))
    create_grids((800, "meters", "4PM-8PM"))
    create_grids((800, "meters", "8PM-12AM"))

    create_grids((850, "meters", "All"))
    create_grids((850, "meters", "12AM-4AM"))
    create_grids((850, "meters", "4AM-8AM"))
    create_grids((850, "meters", "8AM-12PM"))
    create_grids((850, "meters", "12PM-4PM"))
    create_grids((850, "meters", "4PM-8PM"))
    create_grids((850, "meters", "8PM-12AM"))

    create_grids((900, "meters", "All"))
    create_grids((900, "meters", "12AM-4AM"))
    create_grids((900, "meters", "4AM-8AM"))
    create_grids((900, "meters", "8AM-12PM"))
    create_grids((900, "meters", "12PM-4PM"))
    create_grids((900, "meters", "4PM-8PM"))
    create_grids((900, "meters", "8PM-12AM"))

    create_grids((950, "meters", "All"))
    create_grids((950, "meters", "12AM-4AM"))
    create_grids((950, "meters", "4AM-8AM"))
    create_grids((950, "meters", "8AM-12PM"))
    create_grids((950, "meters", "12PM-4PM"))
    create_grids((950, "meters", "4PM-8PM"))
    create_grids((950, "meters", "8PM-12AM"))

    create_grids((1, "kilometer", "All"))
    create_grids((1, "kilometer", "12AM-4AM"))
    create_grids((1, "kilometer", "4AM-8AM"))
    create_grids((1, "kilometer", "8AM-12PM"))
    create_grids((1, "kilometer", "12PM-4PM"))
    create_grids((1, "kilometer", "4PM-8PM"))
    create_grids((1, "kilometer", "8PM-12AM"))

    create_grids((1, "mile", "All"))
    create_grids((1, "mile", "12AM-4AM"))
    create_grids((1, "mile", "4AM-8AM"))
    create_grids((1, "mile", "8AM-12PM"))
    create_grids((1, "mile", "12PM-4PM"))
    create_grids((1, "mile", "4PM-8PM"))
    create_grids((1, "mile", "8PM-12AM"))

    return "Files Created", 200

def create_grids(element):
    polygon_list = []

    file_name = str(element[0]) + element[1] + element[2]

    distance = get_meters(element[0], element[1])
    print("distance", distance)
    grid = create_histogram_grid(distance)
    interval = element[2]
    grid_geojson = grid.to_json()
    grid_geojson_parsed = json.loads(grid_geojson)
    polygon = extract_coordinates(grid_geojson_parsed)
    # print("polygon", polygon)
    polygon_list.append(polygon)

    dial_list = get_count_of_grid_dial(polygon, interval)
    json_dial_list = jsonify(dial_list)
    dial_response = get_data(json_dial_list)
    create_dial_json(dial_response[0], dial_response[1], file_name)

def create_interval_for_dial(grid):
    # return unique elements from list
    grid_set = set(grid)
    sorted_grid_set = sorted(grid_set)
    number_of_intervals = 3
    split_data = np.array_split(sorted_grid_set, number_of_intervals)
    return split_data

def compute_range_percentage(count, countlist):
    arr = np.array(countlist)
    arr_length = len(arr)
    if arr_length == 0:
        return ["unknown", 0]
    if count < 50:
        safe_percentage = np.sum((arr >= 0) & (arr <= 50)) / arr_length * 100
        category = "safest"
    elif 50 <= count < 100:
        safe_percentage = np.sum((arr >= 50) & (arr < 100)) / arr_length * 100
        category = "moderate"
    else:
        safe_percentage = np.sum(arr >= 100) / arr_length * 100
        category = "unsafe"
    rounded_percentage = round(safe_percentage, 2)
    return [category, rounded_percentage]

'''
test section
testing grid creation for the heatmap and histogram charts
'''
@app.route('/testhistogramgrid')
def test_histogram_grid():
    logging.info("Entering test grids route")
    distance = get_meters(500, "meters")
    grid = create_histogram_grid(distance)
    grid_geojson = grid.to_json()
    grid_geojson_parsed = json.loads(grid_geojson)

    return render_template('gridmap.html', polygon=grid_geojson_parsed, key=key)
#
@app.route('/testheatmapgrid')
def test_heatmap_grid():
    point = (39.1318613, -84.51576195582436)
    distance = get_meters(700, "meters")
    grid = create_grid_heatmap(distance, point[0], point[1])
    grid_geojson = grid.to_json()
    grid_geojson_parsed = json.loads(grid_geojson)
    print("grid_geojson_parsed", grid_geojson_parsed)

    return render_template('gridmap.html', polygon=grid_geojson_parsed, key=key)

@app.route('/testpolygon')
def test_polygon():
    point = (39.1318613, -84.51576195582436)
    distance = get_meters(700, "meters")
    polygon = create_polygon(distance, point[0], point[1])
    polygon_geojson = polygon.to_json()
    polygon_parsed = json.loads(polygon_geojson)
    print("grid_geojson_parsed", polygon_parsed)

    return render_template('gridmap.html', polygon=polygon_parsed, key=key)


#Cache for geocoder
def get_geocoded_value(address):
    cached_value = GeocodeCache.objects(address=address).first()

    if cached_value and 'latitude' in cached_value and 'longitude' in cached_value:
        print("---found in cached value ----")
        return cached_value.latitude, cached_value.longitude
    else:
        geolocator = Nominatim(user_agent="project-flask", timeout=10)
        location = geolocator.geocode(address)
        if location:
            GeocodeCache(
                address=address,
                latitude=location.latitude,
                longitude=location.longitude
            ).save()
            print("---performed caching and stored----")
            return location.latitude, location.longitude
        else:
            return None, None

#Main function
@app.route('/success/<safe>/<work>/<current>/<destination>/<interval>/<gridsize>')
def success(safe, work, current, destination, interval, gridsize):
    requests_cache.install_cache(os.path.join(log_dir,'geolocator_cache'), expire_after=3600)
    try:
        total_start_time = time.time()
        start_time = time.time()

        #call the function with the safe location
        safe_latitude, safe_longitude = get_geocoded_value(safe)
        if safe_latitude and safe_longitude:
            print(f"Geocoded coordinates for 'Safe location': Latitude={safe_latitude}, Longitude ={safe_longitude}")
        else:
            print(f"Could not geocode the safe location: {safe}")

        work_latitude, work_longitude = get_geocoded_value(work)
        if work_latitude and work_longitude:
            print(f"Geocoded coordinates for 'Work location': Latitude={work_latitude}, Longitude ={work_longitude}")
        else:
            print(f"Could not geocode the work location: {work}")

        current_latitude, current_longitude = get_geocoded_value(current)
        if current_latitude and current_longitude:
            print(f"Geocoded coordinates for 'current location': Latitude={current_latitude}, Longitude ={current_longitude}")
        else:
            print(f"Could not geocode the current location: {current}")


        destination_latitude, destination_longitude = get_geocoded_value(destination)
        if destination_latitude and destination_longitude:
            print(f"Geocoded coordinates for 'destination location': Latitude={destination_latitude}, Longitude ={destination_longitude}")
        else:
            print(f"Could not geocode the destination location: {destination}")

        logger.info("--- geocoding time: %s seconds --- ", time.time() - start_time)

        start_time = time.time()
        user = UserData()

        user.add_safe_coordinates(safe_latitude, safe_longitude)
        user.add_work_coordinates(work_latitude, work_longitude)
        user.add_current_coordinates(current_latitude, current_longitude)
        user.add_destination_coordinates(destination_latitude, destination_longitude)

        gridsplit = gridsize.split()
        radius = gridsplit[0]
        unit = gridsplit[1]

        user.interval = interval
        user.radius = radius
        user.units = unit

        meters = get_meters(user.radius, user.units)
        logger.info("--- assigning class and meter conversions %s seconds ---", time.time() - start_time)

        safepolygon = create_heatmap_polygon(meters, safe_latitude, safe_longitude)
        workpolygon = create_heatmap_polygon(meters, work_latitude, work_longitude)
        currentpolygon = create_heatmap_polygon(meters, current_latitude, current_longitude)

        destinationpolygon = create_heatmap_polygon(meters, destination_latitude, destination_longitude)

        current_gdf = create_grid_heatmap(meters, current_latitude, current_longitude)
        current_bounds = add_bounds_to_gdf(current_gdf)
        current_west_list = current_bounds['west'].tolist()
        current_east_list = current_bounds['east'].tolist()
        current_north_list = current_bounds['north'].tolist()
        current_south_list = current_bounds['south'].tolist()

        work_gdf = create_grid_heatmap(meters, work_latitude, work_longitude)
        work_bounds = add_bounds_to_gdf(work_gdf)
        work_west_list = work_bounds['west'].tolist()
        work_east_list = work_bounds['east'].tolist()
        work_north_list = work_bounds['north'].tolist()
        work_south_list = work_bounds['south'].tolist()

        safe_gdf = create_grid_heatmap(meters, safe_latitude, safe_longitude)
        safe_bounds = add_bounds_to_gdf(safe_gdf)
        safe_west_list = safe_bounds['west'].tolist()
        safe_east_list = safe_bounds['east'].tolist()
        safe_north_list = safe_bounds['north'].tolist()
        safe_south_list = safe_bounds['south'].tolist()

        destination_gdf = create_grid_heatmap(meters, destination_latitude, destination_longitude)
        destination_bounds = add_bounds_to_gdf(destination_gdf)
        destination_west_list = destination_bounds['west'].tolist()
        destination_east_list = destination_bounds['east'].tolist()
        destination_north_list = destination_bounds['north'].tolist()
        destination_south_list = destination_bounds['south'].tolist()

        current_box = create_box_polygon(meters, current_latitude, current_longitude)
        current_count_dataframe = get_count_of_polygon(current_box, user.interval)
        current_years = current_count_dataframe['Year'].tolist()
        current_counts = current_count_dataframe['Count'].tolist()

        #work
        work_box = create_box_polygon(meters, work_latitude, work_longitude)
        work_count_dataframe = get_count_of_polygon(work_box, user.interval)
        work_years = work_count_dataframe['Year'].tolist()
        work_counts = work_count_dataframe['Count'].tolist()

        # destination
        destination_box = create_box_polygon(meters, destination_latitude, destination_longitude)
        destination_count_dataframe = get_count_of_polygon(destination_box, user.interval)
        destination_years = destination_count_dataframe['Year'].tolist()
        destination_counts = destination_count_dataframe['Count'].tolist()

        # safe
        safe_box = create_box_polygon(meters, safe_latitude, safe_longitude)
        safe_count_dataframe = get_count_of_polygon(safe_box, user.interval)
        safe_years = safe_count_dataframe['Year'].tolist()
        safe_counts = safe_count_dataframe['Count'].tolist()


        safe_count_list = get_count_of_grid_heatmap(safepolygon, user.interval)
        work_count_list = get_count_of_grid_heatmap(workpolygon, user.interval)
        current_count_list = get_count_of_grid_heatmap(currentpolygon, user.interval)
        # print("current_count_list",current_count_list)
        destination_count_list = get_count_of_grid_heatmap(destinationpolygon, user.interval)

        start_time = time.time()
        middle_index = get_middle_element_of_count_list(safe_count_list)
        conditional_safe_center_point_list = [True if index == middle_index else
                                              False for index, num in enumerate(safe_count_list)]
        middle_element_safe_count = safe_count_list[middle_index]
        # print("middle_element_safe_count", middle_element_safe_count)

        middle_index = get_middle_element_of_count_list(work_count_list)
        conditional_work_center_point_list = [True if index == middle_index else
                                              False for index, num in enumerate(work_count_list)]
        middle_element_work_count = work_count_list[middle_index]
        # print("middle_element_work_count", middle_element_work_count)

        middle_index = get_middle_element_of_count_list(current_count_list)
        conditional_current_center_point_list = [True if index == middle_index else
                                                 False for index, num in enumerate(current_count_list)]
        middle_element_current_count = current_count_list[middle_index]
        # print("middle_element_current_count", middle_element_current_count)

        middle_index = get_middle_element_of_count_list(destination_count_list)
        conditional_destination_center_point_list = [True if index == middle_index else
                                                     False for index, num in enumerate(destination_count_list)]
        middle_element_destination_count = destination_count_list[middle_index]
        logger.info("--- middle_element_destination_count : %s seconds ---" , time.time() - start_time)

        start_time = time.time()
        bounding_box_safe = create_bounding_box(user.safecoordinates[1], user.safecoordinates[0], meters)
        bounding_box_current = create_bounding_box(user.currentcoordinates[1], 0, meters)
        bounding_box_work = create_bounding_box(user.workcoordinates[1], user.workcoordinates[0], meters)
        bounding_box_destination = create_bounding_box(user.destinationcoordinates[1], user.destinationcoordinates[0],
                                                       meters)
        logger.info("--- create_bounding_box : %s seconds ---" , time.time() - start_time)

        dial_list = read_dial_grid(gridsize, interval)
        user.grid = dial_list
        interval_lists = create_interval_for_dial(dial_list)
        interval_list1 = interval_lists[0]
        interval_list2 = interval_lists[1]
        interval_list3 = interval_lists[2]

        rows_list = ["A", "A", "A", "A", "A", "B", "B", "B", "B", "B", "C", "C", "C", "C", "C", "D", "D", "D", "D", "D",
                     "E", "E", "E", "E", "E"]
        col_list = ["v1", "v2", "v3", "v4", "v5", "v1", "v2", "v3", "v4", "v5", "v1", "v2", "v3", "v4", "v5",
                    "v1", "v2", "v3", "v4", "v5", "v1", "v2", "v3", "v4", "v5"]

        start_time = time.time()
        safe_dataframe = create_dataframe(rows_list, col_list, safe_count_list, conditional_safe_center_point_list)

        work_dataframe = create_dataframe(rows_list, col_list, work_count_list, conditional_work_center_point_list)

        current_dataframe = create_dataframe(rows_list, col_list, current_count_list,
                                             conditional_current_center_point_list)

        destination_dataframe = create_dataframe(rows_list, col_list, destination_count_list,
                                                 conditional_destination_center_point_list)

        df_safe = pd.DataFrame(safe_dataframe)
        df_work = pd.DataFrame(work_dataframe)
        df_current = pd.DataFrame(current_dataframe)
        df_destination = pd.DataFrame(destination_dataframe)

        # Convert created DataFrame to CSV
        df_safe.to_csv('static/data/heatmap/heatmap_data_safe.csv', index=False)
        df_work.to_csv('static/data/heatmap/heatmap_data_work.csv', index=False)
        df_current.to_csv('static/data/heatmap/heatmap_data_current.csv', index=False)
        df_destination.to_csv('static/data/heatmap/heatmap_data_destination.csv', index=False)

        logger.info("--- create_dataframe : %s seconds ---", time.time() - start_time)

        current_statistic_new = compute_range_percentage(middle_element_current_count, dial_list)
        current_percentage, current_text = current_statistic_new[1], current_statistic_new[0]

        safe_statistic_new = compute_range_percentage(middle_element_safe_count, dial_list)
        safe_percentage, safe_text = safe_statistic_new[1], safe_statistic_new[0]

        work_statistic_new = compute_range_percentage(middle_element_work_count, dial_list)
        work_percentage, work_text = work_statistic_new[1], work_statistic_new[0]

        destination_statistic_new = compute_range_percentage(middle_element_destination_count, dial_list)
        destination_percentage, destination_text = destination_statistic_new[1], destination_statistic_new[0]

        logger.info("=== Total time taken: %s seconds === ", time.time() - total_start_time)

        return render_template('success.html', key=key, mapId=mapId, maxgridelement=max(user.grid),
                               radius=user.radius, units=user.units,
                               interval=user.interval,
                               dial_list=dial_list,
                               years=13,
                               currentaddress=current,
                               safeaddress=safe,
                               workaddress=work,
                               destinationaddress=destination,
                               latsafecoordinate=user.safecoordinates[1],
                               lonsafecoordinate=user.safecoordinates[0],
                               latcurrentcoordinate=user.currentcoordinates[1],
                               loncurrentcoordinate=user.currentcoordinates[0],
                               latworkcoordinate=user.workcoordinates[1],
                               lonworkcoordinate=user.workcoordinates[0],
                               latdestinationcoordinate=user.destinationcoordinates[1],
                               londestinationcoordinate=user.destinationcoordinates[0],
                               middle_element_safe_count=middle_element_safe_count,
                               middle_element_current_count=middle_element_current_count,
                               middle_element_work_count=middle_element_work_count,
                               middle_element_destination_count=middle_element_destination_count,
                               bounding_box_safe=bounding_box_safe,
                               bounding_box_current=bounding_box_current,
                               bounding_box_work=bounding_box_work,
                               bounding_box_destination=bounding_box_destination,
                               intervalOne=interval_list1,
                               intervalTwo=interval_list2,
                               intervalThree=interval_list3,
                               safe_percentage=safe_percentage,
                               current_percentage=current_percentage,
                               work_percentage=work_percentage,
                               destination_percentage=destination_percentage,
                               safe_text=safe_text,
                               current_text=current_text,
                               work_text=work_text,
                               destination_text=destination_text,
                               current_time_years=current_years,
                               current_time_counts=current_counts,
                               work_time_years=work_years,
                               work_time_counts=work_counts,
                               destination_time_years=destination_years,
                               destination_time_counts=destination_counts,
                               safe_time_years=safe_years,
                               safe_time_counts=safe_counts,
                               current_west_list=current_west_list,
                               current_east_list=current_east_list,
                               current_south_list=current_south_list,
                               current_north_list=current_north_list,
                               work_west_list=work_west_list,
                               work_east_list=work_east_list,
                               work_south_list=work_south_list,
                               work_north_list=work_north_list,
                               safe_west_list=safe_west_list,
                               safe_east_list=safe_east_list,
                               safe_south_list=safe_south_list,
                               safe_north_list=safe_north_list,
                               destination_west_list=destination_west_list,
                               destination_east_list=destination_east_list,
                               destination_south_list=destination_south_list,
                               destination_north_list=destination_north_list
                               )
    except GeocoderTimedOut as e:
        flash('Geocoding timed out. Please try again.')
        logger.error("Geocoding timed out. Please try again. %s", str(e))
        return redirect(url_for("home"))
    except Exception as e:
        flash('An error occurred. Please try again.')
        logger.error("An error occurred. Please try again. %s", str(e))
        return redirect(url_for("home"))


@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == 'POST':
        safe = request.form.get("home")
        work = request.form.get("work")
        current = request.form.get("current")
        destination = request.form.get("destination")
        interval = request.form.get("interval")
        gridsize = request.form.get("gridsize")

        if not all([safe, work, current, destination, interval, gridsize]):
            flash("All fields are required. Please fill in every input.")
            logger.error("All fields are required.")
            return redirect(url_for("home"))

        return redirect(url_for('success', safe=safe, work=work, current=current, destination=destination,
                                interval=interval, gridsize=gridsize))

    return render_template("index.html",key=key,mapId=mapId)


if __name__ == '__main__':
    app.run(debug=True)
