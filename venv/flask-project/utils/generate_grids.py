from pyproj import Transformer, Proj, transform
from shapely.geometry import box
import geopandas as gpd


def create_histogram_grid(cell_size_meters):
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


def create_grid_heatmap(distance, latitude, longitude):
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


# for the area chart create bounding box at location
def create_polygon(distance, latitude, longitude):
    transformer_to_3857 = Transformer.from_crs("EPSG:4326", "EPSG:3857")
    transformer_to_4326 = Transformer.from_crs("EPSG:3857", "EPSG:4326")
    # Transform the point to EPSG:3857
    transformed_point_coordinates = transformer_to_3857.transform(latitude, longitude)

    min_x = transformed_point_coordinates[1]
    min_y = transformed_point_coordinates[0]
    max_x = min_x + distance
    max_y = min_y + distance
    min_lon, min_lat = transformer_to_4326.transform(min_y, min_x)
    max_lon, max_lat = transformer_to_4326.transform(max_y, max_x)
    cell_4326 = box(min_lat, min_lon, max_lat, max_lon)
    grid = [cell_4326]
    grid_gdf = gpd.GeoDataFrame(geometry=grid, crs="EPSG:4326")
    return grid_gdf