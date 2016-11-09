import pyproj
import numpy as np
from shapely.geometry import Point, LineString


class GeoType(object):
    def __init__(self, name, values, proj_param, proj_transform_order):
        self.name = name
        self.values = values
        self.proj_param = proj_param
        self.proj_transform_order = [values.index(parameter) for parameter in proj_transform_order]

    def get_proj(self):
        # add additional parameters here
        proj_param = self.proj_param
        proj_param["datum"] = "WGS84"
        return pyproj.Proj(**proj_param)

    # baseline is identity
    def to_utm(self, geo_point):
        return self

    def transform(self, geo_point, to_geo_type):
        args = self.getargs(geo_point)
        if self.proj_param == to_geo_type.proj_param:
            out = args
        else:
            p_from = self.get_proj()
            p_to = to_geo_type.get_proj()
            out = pyproj.transform(p_from, p_to, args[0], args[1])
        array_out = np.array(out)  # just in case its not a numpy already, and will simplify calcs later
        return to_geo_type.post_process(array_out)

    def post_process(self, transformed_points):
        return self.reorder(transformed_points)

    def reorder(self, elements):
        array_elements = np.array(elements)
        if len(array_elements.shape) <= 1:
            post_out = array_elements[self.proj_transform_order]
        else:
            post_out = array_elements[self.proj_transform_order, :]
        return post_out

    def getargs(self, geo_point):
        parameters = self.reorder(self.values)
        return geo_point[parameters[0]], geo_point[parameters[1]]


class UTM(GeoType):
    def __init__(self, zone):
        super(UTM, self).__init__("utm", ["easting", "northing"], {"proj": "utm", "zone": zone},
                                  ["easting", "northing"])


class LatLon(GeoType):
    def __init__(self):
        super(LatLon, self).__init__("latlon", ["latitude", "longitude"], {"proj": "latlong"},
                                     ["longitude", "latitude"])

    def to_utm(self, geo_point):
        np_longitude = np.array(geo_point["longitude"])
        zones = (((np_longitude + 180).round() / 6.0) % 60 + 1).astype(int)
        zone = zones[0] if isinstance(zones, np.ndarray) else zones
        return UTM(zone)


class Cartesian(GeoType):
    def __init__(self, origin, resolution):
        self.zone = origin.utm_reference.proj_param["zone"]
        super(Cartesian, self).__init__("coord", ["x", "y"], {"proj": "utm", "zone": self.zone}, ["x", "y"])
        # doing conversion early on will save use from redoing it later, we don't expect our origin to change too much
        self.origin_easting, self.origin_northing = origin.x, origin.y
        self.resolution = resolution

    def to_utm(self, geo_point):
        return  UTM(self.zone)

    def getargs(self, geo_points):
        # next line should ideally be super.getargs, but we overwrite the fx so not sure if possible
        x, y = geo_points["x"], geo_points["y"]
        delta_easting, delta_northing = np.array([x, y]) * self.resolution
        return self.origin_easting + delta_easting, self.origin_northing - delta_northing

    def post_process(self, transformed_points):
        points_easting, points_northing = transformed_points
        x, y = (points_easting - self.origin_easting, self.origin_northing - points_northing)
        return np.array([np.round(x / self.resolution,6), np.round(y / self.resolution,6)]).astype(int)

class Row_Col(Cartesian):
    def __init__(self, origin, resolution):
        Cartesian.__init__(self, origin, resolution)

    def getargs(self, geo_points):
        arg1, arg2 = Cartesian.getargs(self, geo_points)
        return arg2, arg1

    def post_process(self, transformed_points):
        arg1, arg2 = Cartesian.post_process(self, transformed_points)
        return arg2, arg1

class GeoObject(object):
    def __init__(self, geo_type, x, y):
        self.original_reference = geo_type
        self.data = dict((geo_type.values[idx], val) for idx, val in enumerate([x, y]))

        if geo_type.name == "utm":
            self.easting = x
            self.northing = y
            self.utm_reference = geo_type
        else:
            self.utm_reference = geo_type.to_utm(self.data)
            self.easting, self.northing = geo_type.transform(self.data, self.utm_reference)

    def to(self, other_reference):
        # assuming other_reference is not of type utm
        return self.original_reference.transform(self.data, other_reference)

    def eastingnorthing(self):
        return self.easting, self.northing


class GeoPoint(GeoObject, Point):
    def __init__(self, geo_type, x, y):
        GeoObject.__init__(self, geo_type, x, y)
        Point.__init__(self, self.easting, self.northing)


class GeoPolygon(GeoObject, LineString):
    def __init__(self, firstarg, *args):
        # TODO: not sure if np.array is needed, check
        if isinstance(firstarg, list):
            x= [p.x for p in firstarg]
            y = [p.y for p in firstarg]
            geo_type = firstarg[0].utm_reference
        else:
            x, y = args
            geo_type = firstarg
        GeoObject.__init__(self, geo_type, x, y)
        xytuple = map(tuple, np.array([self.easting, self.northing]).transpose())
        # print xytuple
        LineString.__init__(self, xytuple)

    def geoEnvelope(self):
        env_easting, env_northing = np.array(self.envelope.bounds).reshape((2, 2)).transpose()
        upper_left = GeoPoint(self.utm_reference, env_easting[0], env_northing[1])
        lower_right = GeoPoint(self.utm_reference, env_easting[1], env_northing[0])
        return GeoEnvelope(upper_left, lower_right)


class GeoEnvelope(GeoPolygon):
    def __init__(self, upper_left, lower_right):
        # assum both coordinates are in the same quadrant, choose upper_left by default
        self.upper_left = upper_left
        self.lower_right = lower_right
        GeoPolygon.__init__(self, [upper_left, lower_right])

    def addMargin(self, cartesian_geo_type, margin):
        # margin is in "units" of cartesian_geo_type, aka if 1m resolution, the one unit of margin
        # corresponds to one meter of margin
        upper_left_easting = self.upper_left.easting - margin*cartesian_geo_type.resolution
        upper_left_northing = self.upper_left.northing + margin*cartesian_geo_type.resolution
        lower_right_easting = self.lower_right.easting + margin * cartesian_geo_type.resolution
        lower_right_northing = self.lower_right.northing - margin * cartesian_geo_type.resolution
        new_upper_left = GeoPoint(self.utm_reference, upper_left_easting, upper_left_northing)
        new_lower_right =GeoPoint(self.utm_reference, lower_right_easting, lower_right_northing)
        return GeoEnvelope(new_upper_left, new_lower_right)

    def getBounds(self):
        return self.upper_left, self.lower_right



LAT_LONG = LatLon()
