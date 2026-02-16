import json
import math
from collections import defaultdict, Counter
from datetime import datetime
import logging
from logging.handlers import TimedRotatingFileHandler
from flask import Flask, request, render_template, redirect, url_for, jsonify, flash
import time
from shapely.geometry import box
from pyproj import Transformer, Proj, transform
from geopy.exc import GeocoderTimedOut
import geopandas as gpd
from key import key, mapId
from geopy.geocoders import Nominatim
from dotenv import load_dotenv
import os
from models import db, CustomJSONEncoder, IntervalOne, IntervalTwo, \
    IntervalThree, IntervalFour, IntervalFive, IntervalSix, FilteredModel, GeocodeCache
from models import Model
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor
import requests
import requests_cache


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

#Create indexes for queried fields MR
# IntervalOne.create_index(['point','2dsphere']) #Geospatial index
# IntervalOne.create_index('year')
# IntervalOne.create_index('OFFENSE')
# IntervalOne.create_index([('year', 1), ('OFFENSE', 1)])


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


def load_dataset():
    pipeline = [
        {
            '$addFields': {
                'point': {
                    'type': 'Point',
                    'coordinates': ['$LONGITUDE_X', '$LATITUDE_X']
                }
            }
        }
    ]

    result = Model.objects().aggregate(*pipeline)

    hourpipeline = [
        {
            '$addFields': {
                'hour': {
                    '$substr': ['$time', 0, 2]
                }
            }
        }
    ]

    hour_result = Model.objects().aggregate(*hourpipeline)
    hour_result_list = [doc for doc in hour_result]

    return hour_result_list


def get_difference_in_years():
    combined_pipeline = [
        {
            "$match": {
                "DATE_REPORTED": {
                    "$ne": "",  # Exclude empty strings
                    "$exists": True,  # Ensure the field exists
                    "$type": "string"  # Ensure it's a string
                }
            }
        },
        {
            "$addFields": {
                "year": {
                    "$substr": ["$DATE_REPORTED", 6, 4]
                }
            }
        },
        {
            "$group": {
                "_id": None,
                "maxYear": {"$max": "$year"},
                "minYear": {"$min": "$year"}
            }
        }
    ]
    result = FilteredModel.objects().aggregate(*combined_pipeline)
    result = list(result)
    # print("result0", result[0])
    if result:
        max_year = result[0]["maxYear"]
        min_year = result[0]["minYear"]
        print(f"Max Year: {max_year}, Min Year: {min_year}")
        max_year = int(max_year)
        min_year = int(min_year)
        difference = max_year - min_year
        return difference
    else:
        print("No data found")
        return 0


# filtered_records = Model.objects.filter(INCIDENT_NO__startswith="COPY OF")
# print(f"Filtered records count: {filtered_records.count()}")
# filtered_records.delete()
# print("Records starting with 'COPY OF' have been deleted.")
# print("Updated Model count", Model.objects.count())


# iterate through model objects list
# pass from and to stringField take difference as datetime
# if difference is less than or equal to two filter out model - create new list comprehension to model - NewModel
def update_time_entries_for_model3():
    combined_pipeline = [
        {
            '$addFields': {
                'point': {
                    'type': 'Point',
                    'coordinates': ['$LONGITUDE_X', '$LATITUDE_X']
                }
            }
        }
    ]

    hour_result = Model.objects(INCIDENT_NO__ne=None).aggregate(*combined_pipeline, batchSize=100)
    point_results_new = [
        doc for doc in hour_result
        if doc.get('LONGITUDE_X') != 999999.0 and doc.get('LATITUDE_X') != 999999.0
    ]

    # print("point_results", point_results[0:5])
    # pass in entries foor valid times in 2 intervals

    heavy_crime_list = ['FELONIOUS ASSAULT', 'ASSAULT', 'AGGRAVATED BURGLARY',
                        'AGGRAVATED ROBBERY', 'RAPE', 'ROBBERY', 'MURDER']
    offense_result_new = [result for result in point_results_new if result.get('OFFENSE') in heavy_crime_list]

    print("length of offense_result", len(offense_result_new))
    # print("offense_result", offense_result[0:5])

    DATE_FORMAT = "%m/%d/%Y %I:%M:%S %p"
    # get from and to attributes
    # remove duplicate inccidentNo from modelObjectList
    modelNewObjectList = [item for item in offense_result_new if item.get("INCIDENT_NO") is not None]
    unique_incidents_new = {item['INCIDENT_NO']: item for item in modelNewObjectList}
    print("unique_incidents dictionary length", len(unique_incidents_new))

    for item in unique_incidents_new.values():
        if item.get("DATE_FROM") != "NA" and item.get("DATE_TO") != "NA":
            # convert Stringfield element to date element
            date_from = datetime.strptime(item.get("DATE_FROM"), DATE_FORMAT)
            date_to = datetime.strptime(item.get("DATE_TO"), DATE_FORMAT)
            difference_in_hours = (date_from - date_to).total_seconds() / 3600
            if difference_in_hours <= 2:
                # create midpoint between fromItem and toItem
                midpoint = date_from + (date_to - date_from) / 2
                # print("midpoint", midpoint)
                # pass elements to new model instance
                new_model_instance = FilteredModel(INCIDENT_NO=str(item.get("INCIDENT_NO")),
                                                   MID_DATE=midpoint.strftime(DATE_FORMAT),
                                                   OFFENSE=item.get("OFFENSE"),
                                                   DAYOFWEEK=item.get("DAYOFWEEK"),
                                                   CPD_NEIGHBORHOOD=item.get("CPD_NEIGHBORHOOD"),
                                                   ADDRESS_X=item.get("ADDRESS_X"),
                                                   LONGITUDE_X=item.get("LONGITUDE_X"),
                                                   LATITUDE_X=item.get("LATITUDE_X"),
                                                   point=item.get("point"))
                new_model_instance.save()
    print("NewModel created", FilteredModel.objects.count())


def load_dataset3():
    # create time fields once mid date field is created with value
    combined_pipeline = [
        {
            '$addFields': {
                'time': {
                    '$substr': ['$MID_DATE', 11, 13]
                }
            }
        },
        {
            "$addFields": {
                "ampm": {
                    "$substr": ["$MID_DATE", 20, 2]
                }
            }
        },
        {
            '$addFields': {
                'hour': {
                    '$substr': ['$time', 0, 2]
                }
            }
        }
    ]

    hour_result = FilteredModel.objects(INCIDENT_NO__ne=None).aggregate(*combined_pipeline, batchSize=100)
    hour_result_list = [
        doc for doc in hour_result
    ]
    # print("new model result list updated", hour_result_list[0:3])

    return hour_result_list


def load_dataset2():
    combined_pipeline = [
        {
            '$addFields': {
                'point': {
                    'type': 'Point',
                    'coordinates': ['$LONGITUDE_X', '$LATITUDE_X']
                }
            }
        },
        {
            '$addFields': {
                'time': {
                    '$substr': ['$DATE_REPORTED', 11, 13]
                }
            }
        },
        {
            "$addFields": {
                "ampm": {
                    "$substr": ["$DATE_REPORTED", 20, 2]
                }
            }
        },
        {
            '$addFields': {
                'hour': {
                    '$substr': ['$time', 0, 2]
                }
            }
        }
    ]

    hour_result = Model.objects(INCIDENT_NO__ne=None).aggregate(*combined_pipeline, batchSize=100)
    hour_result_list = [
        doc for doc in hour_result
        if doc.get('LONGITUDE_X') != 999999.0 and doc.get('LATITUDE_X') != 999999.0
    ]
    print("hour_result_list", hour_result_list[0:10])

    return hour_result_list


@app.route('/updateattributes')
def update_attributes():
    try:
        Model.objects(DATE_REPORTED=None).update(set__DATE_REPORTED="NA")
        Model.objects(DATE_FROM=None).update(set__DATE_FROM="NA")
        Model.objects(DATE_TO=None).update(set__DATE_TO="NA")
        Model.objects(OPENING=None).update(set__OPENING="NA")
        Model.objects(LOCATION=None).update(set__LOCATION="NA")
        Model.objects(THEFT_CODE=None).update(set__THEFT_CODE="NA")
        Model.objects(FLOOR=None).update(set__FLOOR="NA")
        Model.objects(WEAPONS=None).update(set__WEAPONS="NA")
        Model.objects(DATE_OF_CLEARANCE=None).update(set__DATE_OF_CLEARANCE="NA")
        Model.objects(ADDRESS_X=None).update(set__ADDRESS_X="NA")
        Model.objects(LONGITUDE_X=None).update(set__LONGITUDE_X=999999.0)
        Model.objects(LATITUDE_X=None).update(set__LATITUDE_X=999999.0)
        return jsonify({"status": "success", "message": 200})
    except Exception as e:
        # Handle exceptions if the update fails
        return jsonify({"status": "error", "message": str(e)}), 500


def filter_time_interval(interval, data):
    if interval == "12AM-4AM":
        newlist = [result for result in data if
                   (result.get('hour') in ['12', '01', '02', '03']) and result.get('ampm') == 'AM']
    elif interval == '4AM-8AM':
        newlist = [result for result in data if
                   (result.get('hour') in ['04', '05', '06', '07']) and result.get('ampm') == 'AM']
    elif interval == "8AM-12PM":
        newlist = [result for result in data if
                   (result.get('hour') in ['08', '09', '10', '11']) and result.get('ampm') == 'AM']
    elif interval == "12PM-4PM":
        newlist = [result for result in data if
                   (result.get('hour') in ['12', '01', '02', '03']) and result.get('ampm') == 'PM']
    elif interval == "4PM-8PM":
        newlist = [result for result in data if
                   (result.get('hour') in ['04', '05', '06', '07']) and result.get('ampm') == 'PM']
    elif interval == "8PM-12AM":
        newlist = [result for result in data if
                   (result.get('hour') in ['08', '09', '10', '11']) and result.get('ampm') == 'PM']
    else:
        newlist = [result for result in data]
        print("newlist0", newlist[0])
    return newlist


def filter_dataset(interval, data):
    # #Filter out crime incidents that do not have a location
    point_results = [result for result in data if result.get('point') != [999999.0, 999999.0]]
    # print("point_results", point_results[0:5])

    # pass in entries foor valid times in 2 intervals

    heavy_crime_list = ['FELONIOUS ASSAULT', 'ASSAULT', 'AGGRAVATED BURGLARY',
                        'AGGRAVATED ROBBERY', 'RAPE', 'ROBBERY', 'MURDER']
    offense_result = [result for result in point_results if result.get('OFFENSE') in heavy_crime_list]
    # print("offense_result", offense_result[0:5])
    filtered_results = filter_time_interval(interval, offense_result)

    return filtered_results


# def get_grid_from_df(fp):
#     # read file path
#     df = pd.read_json(fp)
#     col2_list = df["col2"].tolist()
#     return col2_list

def read_dial_grid(grid, interval):
    PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
    base_path = os.path.join(PROJECT_DIR, 'static', 'data', 'dial')

    file_mapping = {
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
    # Get the file name based on the grid and interval
    file_name = file_mapping.get((grid, interval))
    if not file_name:
        raise ValueError(f"No file mapping found for grid: {grid}, interval: {interval}")
    # Construct the full file path
    file_path = os.path.join(base_path, file_name)
    # Read and return the JSON data
    with open(file_path, 'r') as file:
        data = json.load(file)
        appended_list = data.get("diallist", [])
    return appended_list

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


def compute_range_percentage(count, countlist):
    start_time = time.time()
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
    #print("--- compute_range_percentage: %s seconds ---" % (time.time() - start_time))
    return [category, rounded_percentage]


def compute_range_percentage2(count, countlist):
    start_time = time.time()
    arr = np.array(countlist)
    unique_elements, counts = np.unique(arr, return_counts=True)
    count_dict = defaultdict(int, zip(unique_elements, counts))
    print("count_dict", count_dict)

    arr_length = len(arr)
    print("arr_length:", arr_length)

    if count in range(0, 50):
        print("safe count", count)
        filtered_dict = {key: value for key, value in count_dict.items() if 0 <= key <= 50}
        # print("filtered_dict:", filtered_dict)

        count_sum = sum(filtered_dict.values())
        # print("count_sum:", count_sum)
        safe_percentage = count_sum / arr_length * 100
        # print("safe_percentage:", safe_percentage)
        rounded_up_safe_percentage = math.ceil(safe_percentage * 100) / 100
        # print("rounded_up_safe_percentage:", rounded_up_safe_percentage)
        return_value = ["safest", rounded_up_safe_percentage]
        print("--- compute_range_percentage: %s seconds ---" % (time.time() - start_time))
        return return_value

    elif count in range(50, 100):
        print("moderate count", count)
        filtered_dict = {key: value for key, value in count_dict.items() if 50 <= key < 100}
        count_sum = sum(filtered_dict.values())
        moderate_percentage = count_sum / arr_length * 100
        rounded_up_moderate_percentage = math.ceil(moderate_percentage * 100) / 100
        return ["moderate", rounded_up_moderate_percentage]

    elif count in range(100, max(arr) + 1):
        print("heavy count", count)
        filtered_dict = {key: value for key, value in count_dict.items() if 100 <= key <= max(arr)}
        count_sum = sum(filtered_dict.values())
        heavy_percentage = count_sum / arr_length * 100
        rounded_up_heavy_percentage = math.ceil(heavy_percentage * 100) / 100
        return ["heaviest", rounded_up_heavy_percentage]
    else:
        print("No range found")



def get_middle_element_of_count_list(count_list):
    if len(count_list) % 2 == 0:
        return (len(count_list) // 2) - 1
    else:
        return (len(count_list) - 1) // 2



def get_count_of_grid_dial(polygon_dict, interval):
    start_time = time.time()
    sublists = [polygon_dict[i:i + 5] for i in range(0, len(polygon_dict), 5)]
    # Define a helper function to process each sublist

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


    if interval == "12AM-4AM":
        result = IntervalOne.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        if len(polygon_result_list) != 0:
            # print("polygon_result_list", len(polygon_result_list))
            return len(polygon_result_list)
        else:
            return 0
    elif interval == "4AM-8AM":
        result = IntervalTwo.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        print("polygon_result_list", polygon_result_list)
        if len(polygon_result_list) != 0:
            # print("len_polygon_result_list", len(polygon_result_list))
            return len(polygon_result_list)
        else:
            return 0
    elif interval == "8AM-12PM":
        result = IntervalThree.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        if len(polygon_result_list) != 0:
            # print("polygon_result_list", len(polygon_result_list))
            return len(polygon_result_list)
        else:
            return 0
    elif interval == "12PM-4PM":
        result = IntervalFour.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        if len(polygon_result_list) != 0:
            # print("polygon_result_list", len(polygon_result_list))
            return len(polygon_result_list)
        else:
            return 0
    elif interval == "4PM-8PM":
        result = IntervalFive.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        if len(polygon_result_list) != 0:
            # print("polygon_result_list", len(polygon_result_list))
            return len(polygon_result_list)
        else:
            return 0
    elif interval == "8PM-12AM":
        result = IntervalSix.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        if len(polygon_result_list) != 0:
            # print("polygon_result_list", len(polygon_result_list))
            return len(polygon_result_list)
        else:
            return 0
    else:
        # All intervals
        result = FilteredModel.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        if len(polygon_result_list) != 0:
            # print("foundsublistelement", sublistelement)
            # print("polygon_result_list", polygon_result_list)
            return len(polygon_result_list)
        else:
            return 0


def get_count_of_grid_histogram(polygon_dict, interval):
    sublists = [polygon_dict[i:i + 5] for i in range(0, len(polygon_dict), 5)]
    polygon_list = [search_within_polygon_histogram(sublist, interval) for sublist in sublists]
    print("polygon_list", polygon_list)
    return polygon_list


def search_within_polygon_histogram(sublistelement, interval):
    polygon_pipeline = [
        {
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
        }
    ]

    if interval == "12AM-4AM":
        result = IntervalOne.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        if len(polygon_result_list) != 0:
            # print("polygon_result_list", len(polygon_result_list))
            return polygon_result_list
        else:
            # with open("not_found_sublist_elements.txt", "a") as file:
            #     file.write(f"notfoundsublistelement: {sublistelement}\n")
            return 0
    elif interval == "4AM-8AM":
        result = IntervalTwo.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        # print("polygon_result_list", polygon_result_list)
        if len(polygon_result_list) != 0:
            # print("len_polygon_result_list", len(polygon_result_list))
            return polygon_result_list
        else:
            return 0
    elif interval == "8AM-12PM":
        result = IntervalThree.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        if len(polygon_result_list) != 0:
            # print("polygon_result_list", len(polygon_result_list))
            return polygon_result_list
        else:
            return 0
    elif interval == "12PM-4PM":
        result = IntervalFour.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        if len(polygon_result_list) != 0:
            # print("polygon_result_list", len(polygon_result_list))
            return polygon_result_list
        else:
            return 0
    elif interval == "4PM-8PM":
        result = IntervalFive.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        if len(polygon_result_list) != 0:
            # print("polygon_result_list", len(polygon_result_list))
            return polygon_result_list
        else:
            return 0
    elif interval == "8PM-12AM":
        result = IntervalSix.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        if len(polygon_result_list) != 0:
            # print("polygon_result_list", len(polygon_result_list))
            return polygon_result_list
        else:
            return 0
    else:
        # All intervals
        # element = [e for e in Model.objects()]
        # print("First element", element[0])
        result = FilteredModel.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        if len(polygon_result_list) != 0:
            # print("foundsublistelement", sublistelement)
            # print("polygon_result_list", polygon_result_list)
            return polygon_result_list
        else:
            # print("notfoundelement", sublistelement)
            return 0


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


def search_within_polygon(sublistelement, interval):
    # print("sublistelement", sublistelement)

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

    if interval == "12AM-4AM":
        result = IntervalOne.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        # if len(polygon_result_list) != 0:
        #     # print("new_data_list", polygon_result_list[0])
        #     print("polygon_result_list", len(polygon_result_list))
        return polygon_result_list
    elif interval == "4AM-8AM":
        result = IntervalTwo.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        # if len(polygon_result_list) != 0:
        #     # print("new_data_list", polygon_result_list[0])
        #     print("polygon_result_list", len(polygon_result_list))
        return polygon_result_list
    elif interval == "8AM-12PM":
        result = IntervalThree.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        # if len(polygon_result_list) != 0:
        #     # print("new_data_list", polygon_result_list[0])
        #     print("polygon_result_list", len(polygon_result_list))
        return polygon_result_list
    elif interval == "12PM-4PM":
        polygon_pipeline.append({
            "$sort": {"MID_DATE": 1}
        })
        result = IntervalFour.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        # if len(polygon_result_list) != 0:
        #     # print("new_data_list", polygon_result_list[0])
        #     print("polygon_result_list", len(polygon_result_list))
        return polygon_result_list
    elif interval == "4PM-8PM":
        result = IntervalFive.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        # if len(polygon_result_list) != 0:
        #     # print("new_data_list", polygon_result_list[0])
        #     print("polygon_result_list", len(polygon_result_list))
        return polygon_result_list
    elif interval == "8PM-12AM":
        result = IntervalSix.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        # if len(polygon_result_list) != 0:
        #     # print("new_data_list", polygon_result_list[0])
        #     print("polygon_result_list", len(polygon_result_list))
        return polygon_result_list
    else:
        # All intervals
        result = FilteredModel.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        # if len(polygon_result_list) != 0:
        #     # print("returned sublist element", sublistelement)
        #     # print("new_data_list", polygon_result_list[0])
        #     print("polygon_result_list", len(polygon_result_list))
        return polygon_result_list



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

    if interval == "12AM-4AM":
        result = IntervalOne.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        if len(polygon_result_list) != 0:
            # print("polygon_result_list", len(polygon_result_list))
            return len(polygon_result_list)
        else:
            return 0
    elif interval == "4AM-8AM":
        result = IntervalTwo.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        if len(polygon_result_list) != 0:
            # print("polygon_result_list", len(polygon_result_list))
            return len(polygon_result_list)
        else:
            return 0
    elif interval == "8AM-12PM":
        result = IntervalThree.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        if len(polygon_result_list) != 0:
            # print("polygon_result_list", len(polygon_result_list))
            return len(polygon_result_list)
        else:
            return 0
    elif interval == "12PM-4PM":
        result = IntervalFour.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        if len(polygon_result_list) != 0:
            # print("polygon_result_list", len(polygon_result_list))
            return len(polygon_result_list)
        else:
            return 0
    elif interval == "4PM-8PM":
        result = IntervalFive.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        if len(polygon_result_list) != 0:
            # print("polygon_result_list", len(polygon_result_list))
            return len(polygon_result_list)
        else:
            return 0
    elif interval == "8PM-12AM":
        result = IntervalSix.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        if len(polygon_result_list) != 0:
            # print("polygon_result_list", len(polygon_result_list))
            return len(polygon_result_list)
        else:
            return 0
    else:
        # All intervals
        result = FilteredModel.objects.aggregate(*polygon_pipeline)
        polygon_result_list = [doc for doc in result]
        if len(polygon_result_list) != 0:
            # print("polygon_result_list", len(polygon_result_list))
            return len(polygon_result_list)
        else:
            return 0


def reverse_coordinates(geojson):
    reversed_list = []
    listcount = 0
    feature = geojson['features']

    for f in feature:
        geometry = f['geometry']
        coordinates = geometry['coordinates']
        for inner_list in coordinates:
            for item in inner_list:
                # reversed_list.append(item[::-1])
                reversed_list.append(item)
                listcount += 1
        [reversed_list[listcount: (1 + listcount) * 5]]

    return reversed_list


def create_grid2(cell_size_meters):
    min_lon, min_lat = -84.8192049318631, 39.0533271607855
    max_lon, max_lat = -84.2545822217415, 39.3599982625544

    transformer4326 = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)

    min_x, min_y = transformer4326.transform(min_lon, min_lat)
    max_x, max_y = transformer4326.transform(max_lon, max_lat)

    bbox_3857 = (min_x, min_y, max_x, max_y)

    cell_width_3857 = cell_size_meters
    cell_height_3857 = cell_size_meters

    num_cols = int((bbox_3857[2] - bbox_3857[0]) // cell_width_3857)
    num_rows = int((bbox_3857[3] - bbox_3857[1]) // cell_height_3857)

    grid = []
    for i in range(num_rows):
        for j in range(num_cols):
            x_min = bbox_3857[0] + j * cell_width_3857
            y_min = bbox_3857[1] + i * cell_height_3857
            x_max = x_min + cell_width_3857
            y_max = y_min + cell_height_3857

            cell_3857 = box(x_min, y_min, x_max, y_max)
            grid.append(cell_3857)

    grid_gdf = gpd.GeoDataFrame(geometry=grid, crs="EPSG:3857")

    return grid_gdf.to_crs("EPSG:4326")


def create_interval_for_dial(grid):
    # return unique elements from list
    start_time = time.time()
    grid_set = set(grid)
    sorted_grid_set = sorted(grid_set)
    number_of_intervals = 3
    split_data = np.array_split(sorted_grid_set, number_of_intervals)
    #print("--- create_interval_for_dial: %s seconds ---" % (time.time() - start_time))
    return split_data


def create_bounding_box(latitude, longitude, distance):
    deg_per_meter_lat = 1 / 111320  # Approx. 1 degree latitude = 111.32 km
    lat_diff = distance * deg_per_meter_lat

    deg_per_meter_lon = 1 / (111320 * math.cos(math.radians(latitude)))
    lon_diff = distance * deg_per_meter_lon

    return {
        "west": longitude - lon_diff,
        "east": longitude + lon_diff,
        "south": latitude - lat_diff,
        "north": latitude + lat_diff
    }


def add_bounds_to_gdf(grid_gdf):
    # Extract bounds: minx (west), miny (south), maxx (east), maxy (north)
    bounds = grid_gdf.geometry.bounds
    grid_gdf["west"] = bounds["minx"]
    grid_gdf["south"] = bounds["miny"]
    grid_gdf["east"] = bounds["maxx"]
    grid_gdf["north"] = bounds["maxy"]
    return grid_gdf


def create_polygon(distance, latitude, longitude):
    transformer_to_3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857")
    transformer_to_4326 = Transformer.from_crs("EPSG:3857", "EPSG:4326")
    # Transform the point to EPSG:3857
    transformed_point_coordinates = transformer_to_3857.transform(latitude, longitude)
    min_x = transformed_point_coordinates[1] + distance
    min_y = transformed_point_coordinates[0] + distance
    max_x = min_x + distance
    max_y = min_y + distance
    min_lon, min_lat = transformer_to_4326.transform(min_y, min_x)
    max_lon, max_lat = transformer_to_4326.transform(max_y, max_x)
    cell_4326 = box(min_lat, min_lon, max_lat, max_lon)
    grid = [cell_4326]
    grid_gdf = gpd.GeoDataFrame(geometry=grid, crs="EPSG:4326")
    return grid_gdf


def create_grid_heatmap_new(distance, latitude, longitude):
    # Transform the bounding box to EPSG:3857
    # from point compute bounding box using distance

    transformer_to_3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857")
    transformer_to_4326 = Transformer.from_crs("EPSG:3857", "EPSG:4326")

    # Transform the point to EPSG:3857
    transformed_point_coordinates = transformer_to_3857.transform(latitude, longitude)

    grid_size = 5
    half_grid_size = grid_size // 2

    # Create a grid of squares
    grid = []
    for i in range(-half_grid_size, half_grid_size + 1):
        for j in range(-half_grid_size, half_grid_size + 1):
            min_x = transformed_point_coordinates[1] + (i * distance)
            min_y = transformed_point_coordinates[0] + (j * distance)
            max_x = min_x + distance
            max_y = min_y + distance

            # Transform the EPSG:3857 coordinates back to EPSG:4326
            min_lon, min_lat = transformer_to_4326.transform(min_y, min_x)
            max_lon, max_lat = transformer_to_4326.transform(max_y, max_x)

            # Create a Shapely geometry box for the current grid cell in EPSG:4326
            cell_4326 = box(min_lat, min_lon, max_lat, max_lon)
            grid.append(cell_4326)

    # Create a GeoDataFrame from the grid cells
    grid_gdf = gpd.GeoDataFrame(geometry=grid, crs="EPSG:4326")

    return grid_gdf


# new function updated with bbox
# Function to create grid heatmap based on transformed bounding box
def create_grid_heatmap_new_bbox(distance, latitude, longitude):

    # Calculate bounding box using the create_bounding_box function
    bbox = create_bounding_box(latitude, longitude, distance)

    # Extract the bounding box coordinates
    min_lat = bbox["south"]
    max_lat = bbox["north"]
    min_lon = bbox["west"]
    max_lon = bbox["east"]

    print("Bounding box:", bbox)

    # Create transformers for coordinate conversions
    transformer_to_3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857")
    transformer_to_4326 = Transformer.from_crs("EPSG:3857", "EPSG:4326")

    # Transform the bounding box coordinates to EPSG:3857
    min_x, min_y = transformer_to_3857.transform(min_lat, min_lon)
    max_x, max_y = transformer_to_3857.transform(max_lat, max_lon)

    grid_size = 5
    half_grid_size = grid_size // 2

    # Create a grid of squares
    grid = []
    for i in range(-half_grid_size, half_grid_size + 1):
        for j in range(-half_grid_size, half_grid_size + 1):
            cell_min_x = min_x + (i * distance)
            cell_min_y = min_y + (j * distance)
            cell_max_x = cell_min_x + distance
            cell_max_y = cell_min_y + distance

            # Transform the EPSG:3857 coordinates back to EPSG:4326
            min_lon, min_lat = transformer_to_4326.transform(cell_min_y, cell_min_x)
            max_lon, max_lat = transformer_to_4326.transform(cell_max_y, cell_max_x)

            # Create a Shapely geometry box for the current grid cell in EPSG:4326
            cell_4326 = box(min_lon, min_lat, max_lon, max_lat)
            grid.append(cell_4326)

    # Create a GeoDataFrame from the grid cells
    grid_gdf = gpd.GeoDataFrame(geometry=grid, crs="EPSG:4326")

    return grid_gdf


def create_heatmap_polygon(distance, latitude, longitude):
    start_time = time.time()
    grid = create_grid_heatmap_new(distance, latitude, longitude)
    grid_geojson = grid.to_json()
    grid_geojson_parsed = json.loads(grid_geojson)
    polygon = reverse_coordinates(grid_geojson_parsed)
    logger.info("--- create_heatmap_polygon time taken: %s seconds ---", time.time() - start_time)
    return polygon


def create_current_polygon(distance, latitude, longitude):
    start_time = time.time()
    box_polygon = create_polygon(distance, latitude, longitude)
    box_geojson = box_polygon.to_json()
    box_geojson_parsed = json.loads(box_geojson)
    polygon = reverse_coordinates(box_geojson_parsed)
    logger.info("--- create_current_polygon %s seconds ---", time.time() - start_time)
    return polygon

def delete_heatmap_files():
    data_folder = "data"

    heatmap_folder = "heatmap"

    heatmap_files = [
        "heatmap_data_safe.csv",
        "heatmap_data_work.csv",
        "heatmap_data_current.csv",
        "heatmap_data_destination.csv"
    ]

    deleted_files = []
    errors = []

    for file in heatmap_files:
        file_path = os.path.join(PROJECT_DIR, data_folder, heatmap_folder, file)
        print("file_path", file_path)
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                deleted_files.append(file)
            else:
                errors.append(f"{file} does not exist")
        except Exception as e:
            errors.append(f"Error deleting {file}: {str(e)}")


@app.route('/deleteheatmapfile')
def delete_heatmap_file_endpoint():
    # os join base folder with data folder
    # iterate through files in folder and delete

    data_folder = "data"
    heatmap_folder = "heatmap"

    heatmap_files = [
        "heatmap_data_safe.csv",
        "heatmap_data_work.csv",
        "heatmap_data_current.csv",
        "heatmap_data_destination.csv"
    ]

    deleted_files = []
    errors = []

    for file in heatmap_files:
        file_path = os.path.join(PROJECT_DIR, data_folder, heatmap_folder, file)
        print("file_path", file_path)
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                deleted_files.append(file)
            else:
                errors.append(f"{file} does not exist")
        except Exception as e:
            errors.append(f"Error deleting {file}: {str(e)}")

    return jsonify({
        'deleted_files': deleted_files,
        'errors': errors
    }), 200


@app.route('/deletefilteredmodels')
def delete_filtered_models():
    try:
        if IntervalOne.objects().first():
            deleted_count1 = IntervalOne.objects().delete()
        if IntervalTwo.objects().first():
            deleted_count2 = IntervalTwo.objects().delete()
        if IntervalThree.objects().first():
            deleted_count3 = IntervalThree.objects().delete()
        if IntervalFour.objects().first():
            deleted_count4 = IntervalFour.objects().delete()
        if IntervalFive.objects().first():
            deleted_count5 = IntervalFive.objects().delete()
        if IntervalSix.objects().first():
            deleted_count6 = IntervalSix.objects().delete()
        return jsonify({"status": "success", "deleted_count": deleted_count1,
                        "status": "success", "deleted_count": deleted_count2,
                        "status": "success", "deleted_count": deleted_count3,
                        "status": "success", "deleted_count": deleted_count4,
                        "status": "success", "deleted_count": deleted_count5,
                        "status": "success", "deleted_count": deleted_count6,
                        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/createnewfilteredmodels')
def create_new_filtered_models():
    try:
        interval_list = [
            "12AM-4AM",
            "4AM-8AM",
            "8AM-12PM",
            "12PM-4PM",
            "4PM-8PM",
            "8PM-12AM"
        ]

        # update_time_entries_for_model3()
        hour_aggregate_data = load_dataset3()

        for interval in interval_list:
            data = filter_time_interval(interval, hour_aggregate_data)
            if interval == "12AM-4AM":
                for doc in data:
                    new_model_instance = IntervalOne(**doc)
                    new_model_instance.save()
                print("IntervalOne count", IntervalOne.objects.count())
            elif interval == "4AM-8AM":
                for doc in data:
                    new_model_instance = IntervalTwo(**doc)
                    new_model_instance.save()
                print("IntervalTwo count", IntervalTwo.objects.count())
            elif interval == "8AM-12PM":
                for doc in data:
                    new_model_instance = IntervalThree(**doc)
                    new_model_instance.save()
                print("IntervalThree count", IntervalThree.objects.count())
            elif interval == "12PM-4PM":
                for doc in data:
                    new_model_instance = IntervalFour(**doc)
                    new_model_instance.save()
                print("IntervalFour count", IntervalFour.objects.count())
            elif interval == "4PM-8PM":
                for doc in data:
                    new_model_instance = IntervalFive(**doc)
                    new_model_instance.save()
                print("IntervalFive count", IntervalFive.objects.count())
            elif interval == "8PM-12AM":
                for doc in data:
                    new_model_instance = IntervalSix(**doc)
                    new_model_instance.save()
                print("IntervalSix count", IntervalSix.objects.count())
        return jsonify({"status": "success", "message": 200})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/createfilteredmodels')
def create_filtered_models():
    try:
        interval_list = [
            "12AM-4AM",
            # "4AM-8AM",
            #  "8AM-12PM",
            #  "12PM-4PM",
            #  "4PM-8PM",
            #  "8PM-12AM",
            "All"
        ]

        hour_aggregate_data = load_dataset2()
        for interval in interval_list:
            data = filter_dataset(interval, hour_aggregate_data)
            filtered_data_list = [doc for doc in data if
                                  doc.get("INCIDENT_NO") is not None and isinstance(doc["INCIDENT_NO"], str)]
            if interval == "12AM-4AM":
                for doc in filtered_data_list:
                    new_model_instance = IntervalOne(**doc)
                    new_model_instance.save()
            elif interval == "4AM-8AM":
                for doc in filtered_data_list:
                    new_model_instance = IntervalTwo(**doc)
                    new_model_instance.save()
            elif interval == "8AM-12PM":
                for doc in filtered_data_list:
                    new_model_instance = IntervalThree(**doc)
                    new_model_instance.save()
            elif interval == "12PM-4PM":
                for doc in filtered_data_list:
                    new_model_instance = IntervalFour(**doc)
                    new_model_instance.save()
            elif interval == "4PM-8PM":
                for doc in filtered_data_list:
                    new_model_instance = IntervalFive(**doc)
                    new_model_instance.save()
            elif interval == "8PM-12AM":
                for doc in filtered_data_list:
                    new_model_instance = IntervalSix(**doc)
                    new_model_instance.save()
            else:
                for doc in filtered_data_list:
                    new_model_instance = FilteredModel(**doc)
                    new_model_instance.save()

        return jsonify({"status": "success", "message": 200})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


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


def create_dial_json(data_not_none_list, data_none_list, filename):
    # write data_none_list + data_not_none_list to a file
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


def create_bar_json(data_not_none_list, data_none_list, filename):
    neighborhood_result_list = []
    for element in data_not_none_list:
        print("ELEMENT", element)

        if isinstance(element, dict) and 'CPD_NEIGHBORHOOD' in element:
            neighborhood_result_list.append(element['CPD_NEIGHBORHOOD'])
    print("neighborhood_result_list", neighborhood_result_list)
    # group counts by the neighborhood
    set_neighborhood_result_list = set(neighborhood_result_list)
    print("set_neighborhood_result_list", set_neighborhood_result_list)

    filtered_count_list = []
    for k in set_neighborhood_result_list:
        filtered_list = [element for element in data_not_none_list if element.get('CPD_NEIGHBORHOOD') == k]
        print("filtered_list", filtered_list)
        filtered_count = len(filtered_list)
        filtered_count_list.append(filtered_count)

    print("data_none_list", data_none_list)
    print("filtered_count_list", filtered_count_list)

    appended_list = data_none_list + filtered_count_list
    new_neighborhood_list = list(set_neighborhood_result_list)

    print("appended_list", appended_list)

    barjson = {
        "appended_list": appended_list,
        "new_neighborhood_list": new_neighborhood_list
    }

    bar_file_name = os.path.join(PROJECT_DIR, "static", "data", "histogram", filename + ".json")

    # Write JSON data to a file
    if barjson:
        with open(bar_file_name, 'w') as file:
            json.dump(barjson, file, indent=4)
    else:
        print("bar data not created")


def get_data2(response, filename):
    json_str = response.get_data(as_text=True)
    python_dict = json.loads(json_str)
    print("python_dict", python_dict)
    # flattened list
    data_not_none_list = [d for sublist in python_dict for d in (sublist if isinstance(sublist, list) else [sublist]) if
                          d != 0]
    data_none_list = [element for element in python_dict if element == 0]
    print("data_not_none_list", data_not_none_list)

    # write data_none_list + data_not_none_list to a file
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

    neighborhood_result_list = []
    for element in data_not_none_list:
        print("ELEMENT", element)

        if isinstance(element, dict) and 'CPD_NEIGHBORHOOD' in element:
            neighborhood_result_list.append(element['CPD_NEIGHBORHOOD'])
    print("neighborhood_result_list", neighborhood_result_list)
    # group counts by the neighborhood
    set_neighborhood_result_list = set(neighborhood_result_list)
    print("set_neighborhood_result_list", set_neighborhood_result_list)

    filtered_count_list = []
    for k in set_neighborhood_result_list:
        filtered_list = [element for element in data_not_none_list if element.get('CPD_NEIGHBORHOOD') == k]
        print("filtered_list", filtered_list)
        filtered_count = len(filtered_list)
        filtered_count_list.append(filtered_count)

    print("data_none_list", data_none_list)
    print("filtered_count_list", filtered_count_list)

    appended_list = data_none_list + filtered_count_list
    new_neighborhood_list = list(set_neighborhood_result_list)

    print("appended_list", appended_list)

    barjson = {
        "appended_list": appended_list,
        "new_neighborhood_list": new_neighborhood_list
    }

    bar_file_name = os.path.join(PROJECT_DIR, "static", "data", "histogram", filename + ".json")

    # Write JSON data to a file
    if barjson:
        with open(bar_file_name, 'w') as file:
            json.dump(barjson, file, indent=4)
    else:
        print("bar data not created")

    return barjson


@app.route('/createnewgrids')
def create_new_grids():
    # call create_grids(element)
    # element is tuplecle
    # return response files created

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
    grid = create_grid2(distance)
    interval = element[2]
    grid_geojson = grid.to_json()
    grid_geojson_parsed = json.loads(grid_geojson)
    polygon = reverse_coordinates(grid_geojson_parsed)
    # print("polygon", polygon)
    polygon_list.append(polygon)

    # TODO Need to fix the performance - function is called twice
    # histogram_list = get_count_of_grid_histogram(polygon, interval)
    dial_list = get_count_of_grid_dial(polygon, interval)

    json_dial_list = jsonify(dial_list)
    #json_histogram_list = jsonify(histogram_list)

    # array list returns not_noe_list and none_list
    dial_response = get_data(json_dial_list)
    create_dial_json(dial_response[0], dial_response[1], file_name)

    # histogram_response = get_data(json_histogram_list)
    # create_bar_json(histogram_response[0], histogram_response[1], file_name)

    # return get_data2(json_data_list)


# Model add hour field aggregate to police_cinci_data_new
# filter out where point = [0,0]
# Filter out offense list
# iterate through result list and save to new model
# from the new model filter by the interval then apply the polygon_pipeline


@app.route('/testgrids')
def test_grids():
    logging.info("Entering test grids route")
    distance = get_meters(950, "meters")
    grid = create_grid2(distance)
    grid_geojson = grid.to_json()
    grid_geojson_parsed = json.loads(grid_geojson)

    return render_template('gridmap.html', polygon=grid_geojson_parsed, key=key)


@app.route('/testheatmapgrid')
def test_heatmap_grid():
    point = (39.1318613, -84.51576195582436)
    distance = get_meters(700, "meters")
    grid = create_grid_heatmap_new(distance, point[0], point[1])
    grid_geojson = grid.to_json()
    grid_geojson_parsed = json.loads(grid_geojson)
    print("grid_geojson_parsed", grid_geojson_parsed)

    return render_template('gridmap.html', polygon=grid_geojson_parsed, key=key)

def get_geocoded_value(address):
    cached_value = GeocodeCache.objects(address=address).first()

    if cached_value and 'latitude' in cached_value and 'longitude' in cached_value:
        print("---found in cached value ----")
        return cached_value.latitude, cached_value.longitude
    else:
        # Perform geocoding
        geolocator = Nominatim(user_agent="project-flask", timeout=10)
        location = geolocator.geocode(address)
        if location:
            #store the new geo-coded value in the cache
            GeocodeCache(
                address=address,
                latitude=location.latitude,
                longitude=location.longitude
            ).save()
            print("---performed caching and stored----")
            return location.latitude, location.longitude
        else:
            return None, None


@app.route('/success/<safe>/<work>/<current>/<destination>/<interval>/<gridsize>')
def success(safe, work, current, destination, interval, gridsize):
    requests_cache.install_cache(os.path.join(log_dir,'geolocator_cache'), expire_after=3600)
   # geolocator = Nominatim(user_agent="project-flask", timeout=10)
    try:
        total_start_time = time.time()
        start_time = time.time()

        #call the function with the safe location
        safe_latitude, safe_longitude = get_geocoded_value(safe)
        if safe_latitude and safe_longitude:
            print(f"Geocoded coordinates for 'Safe location': Latitude={safe_latitude}, Longitude ={safe_longitude}")
        else:
            print(f"Could not geocode the safe location: {safe}")

        #call the function with the work location
        work_latitude, work_longitude = get_geocoded_value(work)
        if work_latitude and work_longitude:
            print(f"Geocoded coordinates for 'Work location': Latitude={work_latitude}, Longitude ={work_longitude}")
        else:
            print(f"Could not geocode the work location: {work}")

        #call the function with the current location
        current_latitude, current_longitude = get_geocoded_value(current)
        if current_latitude and current_longitude:
            print(f"Geocoded coordinates for 'current location': Latitude={current_latitude}, Longitude ={current_longitude}")
        else:
            print(f"Could not geocode the current location: {current}")


        #call the function with the destination location
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

        current_gdf = create_grid_heatmap_new(meters, current_latitude, current_longitude)
        current_bounds = add_bounds_to_gdf(current_gdf)
        current_west_list = current_bounds['west'].tolist()
        current_east_list = current_bounds['east'].tolist()
        current_north_list = current_bounds['north'].tolist()
        current_south_list = current_bounds['south'].tolist()

        work_gdf = create_grid_heatmap_new(meters, work_latitude, work_longitude)
        work_bounds = add_bounds_to_gdf(work_gdf)
        work_west_list = work_bounds['west'].tolist()
        work_east_list = work_bounds['east'].tolist()
        work_north_list = work_bounds['north'].tolist()
        work_south_list = work_bounds['south'].tolist()

        safe_gdf = create_grid_heatmap_new(meters, safe_latitude, safe_longitude)
        safe_bounds = add_bounds_to_gdf(safe_gdf)
        safe_west_list = safe_bounds['west'].tolist()
        safe_east_list = safe_bounds['east'].tolist()
        safe_north_list = safe_bounds['north'].tolist()
        safe_south_list = safe_bounds['south'].tolist()

        destination_gdf = create_grid_heatmap_new(meters, destination_latitude, destination_longitude)
        destination_bounds = add_bounds_to_gdf(destination_gdf)
        destination_west_list = destination_bounds['west'].tolist()
        destination_east_list = destination_bounds['east'].tolist()
        destination_north_list = destination_bounds['north'].tolist()
        destination_south_list = destination_bounds['south'].tolist()

        current_box = create_current_polygon(meters, current_latitude, current_longitude)
        current_count_dataframe = get_count_of_polygon(current_box, user.interval)
        current_years = current_count_dataframe['Year'].tolist()
        current_counts = current_count_dataframe['Count'].tolist()

        #work
        work_box = create_current_polygon(meters, work_latitude, work_longitude)
        work_count_dataframe = get_count_of_polygon(work_box, user.interval)
        work_years = work_count_dataframe['Year'].tolist()
        work_counts = work_count_dataframe['Count'].tolist()

        # destination
        destination_box = create_current_polygon(meters, destination_latitude, destination_longitude)
        destination_count_dataframe = get_count_of_polygon(destination_box, user.interval)
        destination_years = destination_count_dataframe['Year'].tolist()
        destination_counts = destination_count_dataframe['Count'].tolist()

        # safe
        safe_box = create_current_polygon(meters, safe_latitude, safe_longitude)
        safe_count_dataframe = get_count_of_polygon(safe_box, user.interval)
        safe_years = safe_count_dataframe['Year'].tolist()
        safe_counts = safe_count_dataframe['Count'].tolist()


        safe_count_list = get_count_of_grid_heatmap(safepolygon, user.interval)
        work_count_list = get_count_of_grid_heatmap(workpolygon, user.interval)
        current_count_list = get_count_of_grid_heatmap(currentpolygon, user.interval)
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

        print("safe_percentage:", safe_percentage)
        print("safe_text:", safe_text)

        # number_of_years = get_difference_in_years()
        # print("number_of_years",number_of_years)
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
