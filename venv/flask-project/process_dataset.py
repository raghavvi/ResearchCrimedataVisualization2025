from datetime import datetime
from models import Model, IntervalOne, IntervalTwo, \
    IntervalThree, IntervalFour, IntervalFive, IntervalSix, FilteredModel

# filtered_records = Model.objects.filter(INCIDENT_NO__startswith="COPY OF")
# print(f"Filtered records count: {filtered_records.count()}")
# filtered_records.delete()
# print("Records starting with 'COPY OF' have been deleted.")
# print("Updated Model count", Model.objects.count())

# iterate through model objects list
# pass from and to stringField take difference as datetime
# if difference is less than or equal to two filter out model - create new list comprehension to model - NewModel
def update_time_entries_for_model():
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


def load_dataset():
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

#Call the preprocessing method once
# Model add hour field aggregate to police_cinci_data_new
# filter out where point = [999999,999999]
# Filter out offense list
# iterate through result list and save to new model
# from the new model filter by the interval then apply the polygon_pipeline
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

        update_time_entries_for_model()
        hour_aggregate_data = load_dataset()

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
        return "success"
    except Exception as e:
        print("Error creating create_new_filtered_models")


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
        return "success"
    except Exception as e:
        print("Error updating attributes")

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
        print("successful records deleted")
    except Exception as e:
        print("Failure to delete filtered models")

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

    heavy_crime_list = ['FELONIOUS ASSAULT', 'ASSAULT', 'AGGRAVATED BURGLARY',
                        'AGGRAVATED ROBBERY', 'RAPE', 'ROBBERY', 'MURDER']
    offense_result = [result for result in point_results if result.get('OFFENSE') in heavy_crime_list]
    # print("offense_result", offense_result[0:5])
    filtered_results = filter_time_interval(interval, offense_result)

    return filtered_results

update_attributes()
create_new_filtered_models()