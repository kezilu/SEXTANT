from api import *
from EnvironmentalModel import *

dem_path = 'maps/hwmidres.tif'

import pandas as pd
import json
pd.options.display.max_rows = 5

with open('MD10_EVA10_Stn18_Stn23_X.json') as data_file:
    data = json.load(data_file)
ways_and_segments = data['sequence']
s = pd.DataFrame(ways_and_segments)
waypoints = s[s['type']=='Station']['geometry']
w = waypoints.values.tolist()
latlongFull = pd.DataFrame(w)
latlongInter = latlongFull['coordinates'].values.tolist()
latlong = pd.DataFrame(latlongInter, columns=['longitude','latitude'])

latlongcoord = LatLongCoord(latlong['latitude'].values,latlong['longitude'].values)

utm = latLongToUTM(latlongcoord)
utmmaxx, utmminx = utm.easting.max(), utm.easting.min()
utmmaxy, utmminy = utm.northing.max(), utm.northing.min()

NWCorner = UTMCoord(utmminx, utmmaxy, utm.zone)
SECorner = UTMCoord(utmmaxx, utmminy, utm.zone)
print(NWCorner)
print(SECorner)

dem_map = loadElevationMap(dem_path, nw_corner=NWCorner, se_corner=SECorner)

astronaut = Astronaut(70)
P = Pathfinder(astronaut, dem_map)

lat,lon = latlong[['latitude','longitude']].iloc[8]
print(lat,lon)
latlong1 = LatLongCoord(lat, lon);
utm1 = latLongToUTM(latlong1)
ap1 = ActivityPoint(latlong1, 0)
row1, col1 = dem_map.convertToRowCol(utm1)
print(row1,col1)

lat,lon = latlong[['latitude','longitude']].iloc[9]
latlong2 = LatLongCoord(lat, lon);
utm2 = latLongToUTM(latlong2)
ap2 = ActivityPoint(latlong2, 0)
print(lat,lon)
ap2 = ActivityPoint(LatLongCoord(lat, lon), 0)
row2, col2 = dem_map.convertToRowCol(utm2)
print(row2,col2)

from bokeh.plotting import figure, output_file, show
from bokeh.io import hplot
output_file("lines.html", title="line plot example")

dh, dw = dem_map.elevations.shape
print dw,dh
# create a new plot with a title and axis labels
s1 = figure(title="simple line example", x_axis_label='x', y_axis_label='y', x_range=[0, dh], y_range=[0, dh])
s2 = figure(title="simple line example", x_axis_label='x', y_axis_label='y', x_range=[0, dh], y_range=[0, dh])

# add a line renderer with legend and line thickness
s1.image(image=[dem_map.elevations[::-1,:]], dw=dw, dh=dh, palette="Spectral11")
s2.image(image=[dem_map.obstacles[::-1,:]], dw=dw, dh=dh)
# show the results

final = P.aStarCompletePath([0, 0, 1], [ap1, ap2], 'tuple', [s1, s2], dh)
s1.circle([col1,col2], [dh-row1,dh-row2])
s2.circle([col1,col2], [dh-row1,dh-row2])

p = hplot(s1, s2)
show(p)