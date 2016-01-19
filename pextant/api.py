from EnvironmentalModel import UTMCoord, LatLongCoord, EnvironmentalModel, loadElevationMap
from ExplorationObjective import ActivityPoint
from ExplorerModel import Explorer, Rover, Astronaut
import convenience

import logging
import csv
import heapq
import numpy as np
import math
import json
from scipy.optimize import fmin_slsqp

from osgeo import gdal, osr

logger = logging.getLogger()

class Pathfinder:
	'''
	This class performs the A* path finding algorithm and contains the Cost Functions. Also includes
	capabilities for analysis of a path.
	
	This class still needs performance testing for maps of larger sizes. I don't believe that
	we will be doing anything extremely computationally intensive though.
	
	Current cost functions are Time, Distance, and (Metabolic) Energy. It would be useful to be able to
	optimize on other resources like battery power or water sublimated, but those are significantly more
	difficult because they depend on shadowing and was not implemented by Aaron.
	'''
	def __init__(self, explorer_model, environmental_model):
		self.explorer = explorer_model
		self.map = environmental_model # a 2d array
		
	def _goalTest(self, Node, endNode):
		'''
		Determines the criteria for "reaching" a node. Could potentially be modified to something like
		return distance < 5
		'''
		return Node.state == endNode.state # this should be the same no matter what value we are optimizing on
	
	def _vectorize(self, optimize_on):
		if optimize_on == 'Energy':
			return [0, 0, 1]
		elif optimize_on == 'Time':
			return [0, 1, 0]
		elif optimize_on == 'Distance':
			return [1, 0, 0]
		else:
			return optimize_on
	
	def _aStarCostFunction(self, start_state, end_state, optimize_on):
		'''
		Given the start and end states, returns the cost of travelling between them.
		Allows for states which are not adjacent to each other.
		
		Note that this uses start and end coordinates rather than nodes.
		
		optimize_vector is a list or tuple of length 3, representing the weights of
		Distance, Time, and Energy
		'''
		optimize_vector = self._vectorize(optimize_on)
		
		R = self.map.resolution
		startRow, startCol = start_state
		endRow, endCol = end_state
		startElev, endElev = self.map.getElevation(start_state), self.map.getElevation(end_state)
		
		path_length = R * math.sqrt((startRow-endRow)**2 + (startCol-endCol)**2)
		slope = math.degrees(math.atan((endElev - startElev) / path_length))
		
		distWeight = self.explorer.distance(path_length)*optimize_vector[0]
		timeWeight = self.explorer.time(path_length, slope)*optimize_vector[1]
		energyWeight = self.explorer.energyCost(path_length, slope, self.map.getGravity())*optimize_vector[2]
		
		return distWeight + timeWeight + energyWeight
	
	def _heuristic(self, currentNode, endNode, optimize_on, search_algorithm):
		'''
		A basic heuristic to speed up the search
		'''
		if search_algorithm == "Field D*":
			startRow, startCol = currentNode.coordinates
			endRow, endCol = endNode.coordinates
		elif search_algorithm == "A*":
			startRow, startCol = currentNode.state
			endRow, endCol = endNode.state
		
		optimize_vector = self._vectorize(optimize_on)
		
		h_diagonal = min(abs(startRow-endRow), abs(startCol-endCol)) # max number of diagonal steps that can be taken
		h_straight = abs(startRow-endRow) + abs(startCol-endCol) # Manhattan distance
		
		# D represents the cost between two consecutive nodes
		D = 0
		
		# Adding the energy weight
		m = self.explorer.mass
		R = self.map.resolution
		if self.map.planet == 'Earth':
			D += (1.504 * m + 53.298) * R * optimize_vector[2]
		elif self.map.planet == 'Moon' and self.explorer.type == 'Astronaut':
			D += (2.295 * m + 52.936) * R * optimize_vector[2]
		elif self.map.planet == 'Moon' and self.explorer.type == 'Rover':
			P_e = self.explorer.P_e # this only exists for rovers
			D += (0.216 * m + P_e / 4.167) * R * optimize_vector[2]
		else:
			# This should not happen
			print "Error: something wrong with either the planet or the explorer"
			print "current planet: " + self.map.planet + "; current explorer type: " + self.explorer.type
			return 0
		
		# Adding the distance weight
		D += self.map.resolution * optimize_vector[0]

		# Adding the time weight
		D += self.map.resolution * optimize_vector[1]/1.6 # the maximum velocity is 1.6 from Marquez 2008
		
		if search_algorithm == "A*":
			# Patel 2010. See page 49 of Aaron's thesis
			return D * math.sqrt(2) * h_diagonal + D * (h_straight - 2 * h_diagonal)
		elif search_algorithm == "Field D*":
			# This is just Euclidean distance
			return D * math.sqrt((startRow - endRow)**2 + (startCol - endCol)**2)
	
	def _aStarGetNeighbors(self, searchNode, optimize_on):
		# returns a list of states that can be travelled to
		# basically just the 8 surrounding tiles, if they are inbounds and not obstacles
		row = searchNode.state[0]
		col = searchNode.state[1]
		children = [(row+1, col),(row+1, col+1),(row+1, col-1),(row, col+1),(row, col-1),(row-1, col+1),(row-1, col),(row-1, col-1)]
		valid_children = [child for child in children if self.map.isPassable(child)]
		return [aStarSearchNode(state, searchNode, searchNode.cost + \
			self._aStarCostFunction(searchNode.state, state, optimize_on)) for state in valid_children]
	
	def aStarSearch(self, startNode, endNode, optimize_on):
		'''
		returns the path (as a list of coordinates), followed by the number of
		states expanded, followed by the total cost
	
		this function will return a list of states between two waypoints
		to get the full path between all waypoints, we will basically just use
		this function multiple times with each set
		as long as the number of waypoints is reasonable we should be fine
		
		costFunction supports a vector costFunction, with the
		three vector elements representing: 'Energy', 'Time', or 'Distance'
		As of right now 'Energy' just refers to metabolic energy.
		'''
		if self._goalTest(startNode, endNode):
			return startNode.getPath()
		agenda = []
		heapq.heappush(agenda, (startNode.cost+self._heuristic(startNode, endNode, optimize_on, "A*"), startNode))
																#agenda contains pairs (cost, node)
		expanded = set()
		while len(agenda) > 0:
			priority, node = heapq.heappop(agenda)
			if node.state not in expanded:
				expanded.add(node.state)
				if self._goalTest(node, endNode):
					return (node.getPath(), len(expanded), node.cost)
				for child in self._aStarGetNeighbors(node, optimize_on):
					if child.state not in expanded:
						heapq.heappush(agenda,((child.cost+self._heuristic(child, endNode, optimize_on, "A*"), child)))
		return (None, len(expanded), node.cost)

	def aStarCompletePath(self, optimize_on, waypoints, returnType = "JSON", fileName = None):
		'''
		Returns a tuple representing the path and the total cost of the path.
		The path will be a list. All activity points will be duplicated in
		the returned path.
		
		waypoints is a list of activityPoint objects, in the correct order. fileName is
		used when we would like to write stuff to a file and is currently necessary
		for csv return types.
		'''
		optimize_vector = self._vectorize(optimize_on)
		
		finalPath = []
		costs = []
		for i in range(len(waypoints)-1):
			segmentCost = 0
		
			node1 = aStarSearchNode(self.map.convertToRowCol(waypoints[i].coordinates), None, 0)
			node2 = aStarSearchNode(self.map.convertToRowCol(waypoints[i+1].coordinates), None, 0)
			partialPath = self.aStarSearch(node1, node2, optimize_vector)
						
			path, expanded, cost = partialPath
			
			finalPath += path # for now I'm not going to bother with deleting the duplicates
							  # that will occur at every activity point. I think it might end up being useful
			segmentCost += cost
			segmentCost += optimize_vector[1]*waypoints[i].duration
			
			costs.append(segmentCost)
		if returnType == "tuple":
			return (finalPath, costs, sum(costs))
		elif returnType == "JSON":
			data = self._toJSON(finalPath, optimize_on, waypoints)
			if fileName:
				with open(fileName, 'w') as outfile:
					json.dump(data, outfile, indent = 4)
			return data
		elif returnType == "csv":
			sequence = self._toCSV(finalPath, optimize_on, waypoints)
			print sequence
			if fileName:
				with open(fileName, 'wb') as csvfile:
					writer = csv.writer(csvfile)
					for row in sequence:
						writer.writerow(row)
			return sequence

	def completeSearchFromJSON(self, optimize_on, jsonInput, returnType = "JSON", fileName = None, algorithm = "A*", numTestPoints = 0):
		parsed_json = json.loads(jsonInput)
		new_json = json.loads(jsonInput)
# 		Kevin -- there is no deepcopy, and if you just want the same thing twice we can load it twice ...
# 		new_json = deepcopy(parsed_json)
		waypoints = []
		
		for element in parsed_json: # identify all of the waypoints
			if element["type"] == "Station":
				lon, lat = element["geometry"]["coordinates"]
				time_cost = element["userDuration"]
				waypoints.append(ActivityPoint(LatLongCoord(lat, lon), time_cost, None)) # set the cost to time cost
		if algorithm == "A*":
			path = self.aStarCompletePath(optimize_on, waypoints, returnType, fileName)
		elif algorithm == "Field D*":
			path = self.fieldDStarCompletePath(optimize_on, waypoints, returnType, fileName, numTestPoints)
		
		for i, element in enumerate(new_json):
			if element["type"] == "Segment":
				new_json[i]["derivedInfo"] = path[i]["derivedInfo"]
				new_json[i]["geometry"] = path[i]["geometry"]

		return json.dumps(new_json)
			
#########################################################################################
# Above is A*; below is field D* (see Ferguson and Stentz 2005)							#
#########################################################################################
		
	def _fieldDStarGetNeighbors(self, searchNode):
		row = searchNode.coordinates[0]
		col = searchNode.coordinates[1]
		children = [(row+1, col),(row+1, col+1),(row+1, col-1),(row, col+1),(row, col-1),(row-1, col+1),(row-1, col),(row-1, col-1)]
		valid_children = [child for child in children if self.map.isPassable(child)]
		return valid_children
		
	def _fieldDStarGetConsecutiveNeighbors(self, coordinates):
		'''
		To calculate cost, field D* requires pairs of consecutive neighbors
		
		Note that neighbors are returned as tuples
		'''
		row = coordinates[0]
		col = coordinates[1]
	
		consecutive_neighbors = [((row+1, col), (row+1, col+1)),
								 ((row+1, col+1), (row, col+1)),
								 ((row, col+1), (row-1, col+1)),
								 ((row-1, col+1), (row-1, col)),
								 ((row-1, col), (row-1, col-1)),
								 ((row-1, col-1), (row, col-1)),
								 ((row, col-1), (row+1, col-1)),
								 ((row+1, col-1), (row+1, col))]
		valid_consecutive_neighbors = [item for item in consecutive_neighbors if (self.map._inBounds(item[0]) and self.map._inBounds(item[1]))]
		return valid_consecutive_neighbors
		
	def _fieldDStarComputeCost(self, node, neighbor_a, neighbor_b, optimize_on, nodeDict = None, numTestPoints = 11):
		# neighbor_a and neighbor_b must be nodes, not coordinates
		# This function returns a tuple - the point on the edge that is intersected, and the cost
		# Check the documentation for more information about the Compute Cost function
		optimize_vector = self._vectorize(optimize_on)
		
		R = self.map.resolution
		row, col = 	node.coordinates
		# s_1 is the horizontal neighbor, s_2 is the diagonal neighbor
		if neighbor_a.coordinates[0] == row or neighbor_a.coordinates[1] == col:
			s_1 = neighbor_a
			s_2 = neighbor_b
		else:
			s_1 = neighbor_b
			s_2 = neighbor_a
		
		c_1 = s_1.cost
		h_1 = self.map.getElevation(s_1.coordinates)
		c_2 = s_2.cost
		h_2 = self.map.getElevation(s_2.coordinates)
		h = self.map.getElevation(node.coordinates)
		
		# This takes care of the cases where c_1 or c_2 are infinite
		# In one of these is infinite, we simply take the other path
		if (c_1 == float('inf')) and (c_2 != float('inf')):
			return (s_2.coordinates, self._aStarCostFunction(node.coordinates, s_2.coordinates, optimize_vector) + s_2.cost)
		elif (c_2 == float('inf')) and (c_1 != float('inf')):
			return (s_1.coordinates, self._aStarCostFunction(node.coordinates, s_1.coordinates, optimize_vector) + s_1.cost)
		elif (c_1 == float('inf')) and (c_2 == float('inf')):
			return (s_1.coordinates, float('inf'))
					
		# This is the function which goes directly to the opposite side
		# y represents the y-coordinate of the side and is a number between 0 and 1
		
		def f(y):
			prevCost = y*c_2 + (1-y)*c_1
			height = y*h_2 + (1-y)*h_1
			dist = math.sqrt(1+y**2)*R
			slope = math.degrees(math.atan((height - h) / (dist)))
			
			d = self.explorer.distance(dist)
			t = self.explorer.time(dist, slope)
			e = self.explorer.energyCost(dist, slope, self.map.getGravity())
			
			# This stuff is mostly just here because unfortunately inf*0 = nan
			# and nan is really hard to deal with and causes a lot of bugs
			totalCost = prevCost
			
			if optimize_vector[0] != 0:
				totalCost += d*optimize_vector[0]
			if optimize_vector[1] != 0:
				totalCost += t*optimize_vector[1]
			if optimize_vector[2] != 0:
				totalCost += e*optimize_vector[2]
			
			return totalCost
		
		step = 1.0/(numTestPoints-1)
		# evenly spread test points
		testPoints = [step * i for i in range(numTestPoints)]
		
		# We have several test points to determine our starting location
		funcPoints = [f(tp) for tp in testPoints]
		startPoint = testPoints[np.argmin(funcPoints)]
		
		# Not too sure if SLSQP is the best choice. I chose it because it allows me to set bounds.
		minimum = fmin_slsqp(f, startPoint, bounds = [(0, 1)], iprint = 0)[0]
		
		# point is the point that is corresponds by the minimum value
		point = ((1-minimum)*s_1.coordinates[0] + minimum*s_2.coordinates[0], (1-minimum)*s_1.coordinates[1] + minimum*s_2.coordinates[1])
		
		return (point, f(minimum))
		
	def _fieldDStarGetKey(self, nodeDict, coordinates, start_node, optimize_on):
		# if never visited before, add it to nodeDict
		if coordinates not in nodeDict.keys():
			nodeDict[coordinates] = FieldDStarNode(coordinates, float('inf'), float('inf'))
		
		node = nodeDict[coordinates]
		
		return (min(node.cost, node.rhs) + self._heuristic(node, start_node, optimize_on, "Field D*"), min(node.cost, node.rhs))
		
	def _fieldDStarUpdateState(self, nodeDict, openInputs, startNode, coordinates, endCoordinates, optimize_on):
		# print "State being updated: ", coordinates
		
		# If node was never previously visited, cost = infinity, mark it as visited
		if coordinates not in nodeDict.keys():
			nodeDict[coordinates] = FieldDStarNode(coordinates, float('inf'), float('inf'))
			node = nodeDict[coordinates]
			logger.info('Added coordinate ', coordinates, ' to the nodeDict')
		else:
			node = nodeDict[coordinates]
		
		# If node != goal
		# rhs(node) = min_{(s', s'') in connbrs(node)} ComputeCost(node, s', s'')
		if coordinates != endCoordinates:
			rhs = float('inf')
			for pair in self._fieldDStarGetConsecutiveNeighbors(coordinates):
				neighbor_a, neighbor_b = pair
				
				if neighbor_a not in nodeDict.keys():
					nodeDict[neighbor_a] = FieldDStarNode(neighbor_a)
				if neighbor_b not in nodeDict.keys():
					nodeDict[neighbor_b] = FieldDStarNode(neighbor_b)
				
				test_val = self._fieldDStarComputeCost(node, nodeDict[neighbor_a], nodeDict[neighbor_b], optimize_on)[1]				
				
				if test_val < rhs:
					rhs = test_val
			node.rhs = rhs
		
		# updating nodeDict
		nodeDict[coordinates] = node
		
		# if node in openInputs, remove node from openInputs
		open_coords = [pair[1] for pair in openInputs]
		if coordinates in open_coords:
			for pair in openInputs:
				if pair[1] == coordinates:
					openInputs.remove(pair)
		
		# if cost != rhs, insert node into openInputs with key(node)
		if node.cost != node.rhs:
			heapq.heappush(openInputs, (self._fieldDStarGetKey(nodeDict, coordinates, startNode, optimize_on), coordinates))
	
	def _fieldDStarComputeShortestPath(self, nodeDict, startCoordinates, endCoordinates, openInputs, optimize_on):
		startNode = nodeDict[startCoordinates]
		past_100_coordinates = [None] * 100
		
		while (openInputs[0][0] < self._fieldDStarGetKey(nodeDict, startCoordinates, startNode, optimize_on)) or (startNode.cost != startNode.rhs):
			key, coordinates = heapq.heappop(openInputs)
			# if the coordinate appeared more than 20 times in the past 100 coordinates
			# we skip it and move on to the next thing in openInputs
			if past_100_coordinates.count(coordinates) > 20: 
				key, coordinates = heapq.heappop(openInputs)
			node = nodeDict[coordinates]
			
			past_100_coordinates.pop(0)
			past_100_coordinates.append(coordinates)
			
			# print key, coordinates
			
			if node.cost > node.rhs:
				node.cost = node.rhs
				nodeDict[coordinates] = node
				for neighbor in self._fieldDStarGetNeighbors(node):
					self._fieldDStarUpdateState(nodeDict, openInputs, nodeDict[startCoordinates], neighbor, endCoordinates, optimize_on)
			else:
				node.cost = float('inf')
				nodeDict[coordinates] = node
				for neighbor in self._fieldDStarGetNeighbors(node) + [node.coordinates]:
					self._fieldDStarUpdateState(nodeDict, openInputs, nodeDict[startCoordinates], neighbor, endCoordinates, optimize_on)
					
	def _fieldDStarExtractPath(self, nodeDict, startCoordinates, endCoordinates, optimize_on, numTestPoints = 11):
		coordinates = startCoordinates
		path = [startCoordinates]
		optimize_vector = self._vectorize(optimize_on)
		
		def interpolatedConsecutiveCoordinates(p):
		# This function represents the 6 possible pairs of consecutive points
		# around an interpolated point
			if (p[0]%1 != 0):
				a = int(p[0])
				b = p[1]
				return [((a, b), (a, b+1)),
						((a, b+1), (a+1, b+1)),
						((a+1, b+1), (a+1, b)),
						((a+1, b), (a+1, b-1)),
						((a+1, b-1), (a, b-1)),
						((a, b-1), (a, b))]
			else:
				a = p[0]
				b = int(p[1])
				return [((a, b) ,(a+1, b)),
						((a+1, b), (a+1, b+1)),
						((a+1, b+1), (a, b+1)),
						((a, b+1), (a-1, b+1)),
						((a-1, b+1), (a-1, b)),
						((a-1, b), (a, b))]
						
		# node will always refer to the current point in the path
		while coordinates != endCoordinates:
			print path
			nextPoint = None
		
			if (coordinates[0] % 1 != 0) or (coordinates[1] % 1 != 0): #interpolated point
				
				height = convenience.getWeightedElevation(self.map, coordinates)
				connCoords = interpolatedConsecutiveCoordinates(coordinates)
				
			else: # both coordinates are integers
				height = self.map.getElevation(coordinates)
				connCoords = self._fieldDStarGetConsecutiveNeighbors(coordinates)
			
			# The cost of the current point. We will be minimizing the difference of the point that is travelled to
			# and the cost of the current point.
			currentCost = convenience.getWeightedCost(nodeDict, coordinates)
			
			minCost = float('inf')
			
			# There should be either six or eight pairs; we put the best option into nextPoint with the associated cost into minCost
			for pair in connCoords:
				# making sure that both coordinates in the pair are contained
				# inside the nodeDict
				if (pair[0] in nodeDict) and (pair[1] in nodeDict):
					s_1 = nodeDict[pair[0]]
					s_2 = nodeDict[pair[1]]
				else:
					continue
				
				h_1 = self.map.getElevation(s_1.coordinates)
				h_2 = self.map.getElevation(s_2.coordinates)
				c_1 = s_1.cost
				c_2 = s_2.cost
				
				# First 3 parts deal with what happens when c_1 or c_2 are infinite
				if (c_1 == float('inf')) and (c_2 != float('inf')):
					prevCost = c_2
					newCost = self._aStarCostFunction(coordinates, s_2.coordinates, optimize_vector)
					if abs(prevCost + newCost - currentCost) < minCost:
						minCost = abs(prevCost + newCost - currentCost)
						nextPoint = s_2.coordinates
				elif (c_2 == float('inf')) and (c_1 != float('inf')):
					prevCost = c_1
					newCost = self._aStarCostFunction(coordinates, s_1.coordinates, optimize_vector)
					if abs(prevCost + newCost - currentCost) < minCost:
						minCost = abs(prevCost + newCost - currentCost)
						nextPoint = s_1.coordinates
				elif (c_1 == float('inf')) and (c_2 == float('inf')):
					continue # This is not gonna be viable
				else:
					def f(y):
						# This is the function to be minimized
						
						prevCost = (1-y)*c_1 + y*c_2
						
						coord1, coord2 = pair
						x1, y1 = coord1
						x2, y2 = coord2
						
						if x1 == x2:
							p = (x1, y1*(1-y) + y2*y)
						else:
							p = (x1*(1-y) + x2*y, y2)
						
						h = (1-y)*h_1 + y*h_2
						
						path_length = math.sqrt(((p[0]-coordinates[0])**2) + ((p[1]-coordinates[1])**2)) * self.map.resolution
						slope = math.degrees(math.atan((height - h) / path_length))
						grav = self.map.getGravity()
						
						result = prevCost - currentCost
						
						# This stuff is necessary to avoid some annoying inf*0 = nan thing
						
						if optimize_vector[0] != 0:
							result += self.explorer.distance(path_length)*optimize_vector[0]
						if optimize_vector[1] != 0:
							result += self.explorer.time(path_length, slope)*optimize_vector[1]
						if optimize_vector[2] != 0:
							result += self.explorer.energyCost(path_length, slope, grav)*optimize_vector[2]
						
						return result
					
					#We test 11 points and choose the smallest one as the "seed value" for the minimization
					step = 1.0/(numTestPoints-1)
					
					testPoints = [step * i for i in range(numTestPoints)]
					fPoints = [f(tp) for tp in testPoints]
					startPoint = testPoints[np.argmin(fPoints)]
					
					minimum = fmin_slsqp(f, startPoint, bounds = [(0, 1)], iprint = 0)[0]
					minResult = f(minimum)
					
					if minResult < minCost:
						minCost = minResult
						nextPoint = ((1-minimum)*pair[0][0] + minimum*pair[1][0], (1-minimum)*pair[0][1] + minimum*pair[1][1])
			
			if nextPoint:
				# if nextPoint exists, add it
				path.append(nextPoint)
				coordinates = nextPoint
			else:
				print "nextPoint doesn't exist!"
				break
		
		return path
	
	def fieldDStarSearch(self, startCoords, endCoords, optimize_on, numTestPoints = 11):
		optimize_vector = self._vectorize(optimize_on)
		
		startNode = FieldDStarNode(startCoords, float('inf'), float('inf'))
		endNode = FieldDStarNode(endCoords, float('inf'), 0)
		
		open_coords = []
		nodeDict = {} # This dictionary maps coordinates to FieldDStarNode objects.
					  # Only contains nodes that have been travelled to/are relevant
		nodeDict[startCoords] = startNode
		nodeDict[endCoords] = endNode
		
		heapq.heappush(open_coords, (self._fieldDStarGetKey(nodeDict, endCoords, endNode, optimize_vector), endCoords))
		self._fieldDStarComputeShortestPath(nodeDict, startCoords, endCoords, open_coords, optimize_vector)
		
		for key in nodeDict:
			print '{',key[0],',', key[1],',', nodeDict[key].cost,'},'
			
		path = self._fieldDStarExtractPath(nodeDict, startCoords, endCoords, optimize_vector, numTestPoints)
		# return self._toJSON(path, optimize_vector, [ActivityPoint(startCoords), ActivityPoint(endCoords)])
		return path
	
	def fieldDStarCompletePath(self, optimize_on, waypoints, returnType = "JSON", fileName = None, numTestPoints = 11):
		optimize_vector = self._vectorize(optimize_on)
		
		finalPath = []
		costs = []
		for i in range(len(waypoints)-1):
			segmentCost = 0
		
			p1 = self.map.convertToRowCol(waypoints[i].coordinates)
			p2 = self.map.convertToRowCol(waypoints[i+1].coordinates)
			
			partialPath = self.fieldDStarSearch(p1, p2, optimize_vector, numTestPoints)
			
			path, expanded, cost = partialPath
			
			finalPath += path # for now I'm not going to bother with deleting the duplicates
							  # that will occur at every activity point. I think it might end up being useful
			segmentCost += cost
			segmentCost += optimize_vector[1]*waypoints[i].duration
			
			costs.append(segmentCost)
		if returnType == "tuple":
			return (finalPath, costs, sum(costs))
		elif returnType == "JSON":
			data = self._toJSON(finalPath, optimize_on, waypoints)
			if fileName:
				with open('data.json', 'w') as outfile:
					json.dump(data, outfile, indent = 4)
			return data
		elif returnType == "csv":
			sequence = self._toCSV(finalPath, optimize_on, waypoints)
			print sequence
			if fileName:
				with open(fileName, 'wb') as csvfile:
					writer = csv.writer(csvfile)
					for row in sequence:
						writer.writerow(row)
			return sequence
			
	def _toJSON(self, path, optimize_on, waypoints):
		'''
		This returns the list following "sequence." It doesn't really make sense
		for SEXTANT to actually do parts of the JSON file such as "creator."
		'''		
		sequence = []
		def empty():
			return {
						"geometry": {
							"type": "LineString",
							"coordinates": []
									},
						"derivedInfo": {
							"distanceList": [],
							"energyList": [],
							"timeList": [],
							"totalDistance": 0,
							"totalTime": 0,
							
							"totalEnergy": 0
										 },
						"type": "Segment"
					}
		lineString = {}
		
		def nextStation(lineString, firstStation = False): #firstStation denotes if the station is the first of the path
			AP = waypoints.pop(0)
			element = {}
			
			latLong = self.map.convertToLatLong(AP.coordinates)
			
			element["geometry"] = {"coordinates": latLong.longLatList(), "type": "Point"}
			element["type"] = "Station"
			element["UUID"] = AP.UUID
			
			# When we hit a station, we need to add the previous lineString to the sequence,
			# compute the total Dist, Time, and Energy
			if not firstStation:
				lineString["derivedInfo"]["totalDistance"] = sum(lineString["derivedInfo"]["distanceList"])
				lineString["derivedInfo"]["totalTime"] = sum(lineString["derivedInfo"]["timeList"])
				lineString["derivedInfo"]["totalEnergy"] = sum(lineString["derivedInfo"]["energyList"])
				
				sequence.append(lineString)
				
			return element
		
		for i, point in enumerate(path):
			# first set of if/elif statements creates the "Station" or "pathPoint" things,
			# second set of if/elif statements creates the "segments" connecting them
			if i == 0: # first element is a station
				sequence.append(nextStation(lineString, True))
				lineString = empty()
				lineString["geometry"]["coordinates"].append(self.map.convertToLatLong(point).longLatList())
			elif point == path[i-1]: # point is identical to the previous one and is thus a station
				sequence.append(nextStation(lineString))
				lineString = empty()
				lineString["geometry"]["coordinates"].append(self.map.convertToLatLong(point).longLatList())
			else: # This point is not a station and is thus a point inside the path
				latLongCoords = self.map.convertToLatLong(point).longLatList()
				
				lineString["geometry"]["coordinates"].append(latLongCoords)
				
				startState, endState = path[i-1], path[i]
				distance = self._aStarCostFunction(startState, endState, [1, 0, 0]) # distance
				energy = self._aStarCostFunction(startState, endState, [0, 0, 1]) # energy
				time = self._aStarCostFunction(startState, endState, [0, 1, 0]) # time
				
				lineString["derivedInfo"]["distanceList"].append(distance)
				lineString["derivedInfo"]["energyList"].append(energy)
				lineString["derivedInfo"]["timeList"].append(time)
		
		# Call nextStation at the end to make the final point a station
		sequence.append(nextStation(lineString))
		
		return sequence
		
	def _toCSV(self, path, optimize_on, waypoints):
		
		sequence = []
		
		def nextStation():
			AP = waypoints.pop(0)
			
			latLong = self.map.convertToLatLong(AP.coordinates)
			elev = self.map.getElevation(AP.coordinates)
			
			return [True, latLong.longitude, latLong.latitude, elev]
		
		for i, point in enumerate(path):
			row = []
			# first set of if/elif statements creates the "Station" or "pathPoint" things,
			# second set of if/elif statements creates the "segments" connecting them
			if i == 0 or i == len(path) - 1: # first and last elements are always station
				row += nextStation()
			elif point == path[i+1]: # point is identical to the next one and is thus a station
				pass
			elif point == path[i-1]:
				row += nextStation()
			else: # This point is not a station and is thus a "pathPoint," basically just a point inside the path
				latLongCoords = [self.map.convertToLatLong(point).longitude, self.map.convertToLatLong(point).latitude]
				elev = self.map.getElevation(point)
				row += ([False] + latLongCoords + [elev])
				
			if i == (len(path)-1): # We have reached the end of the path
				pass
			elif path[i] == path[i+1]: # This indicates the first coordinate in a station
				pass # We add nothing to the csv
			else: # Otherwise we need to add a segment
				startState, endState = path[i], path[i+1]
				distance = self._aStarCostFunction(startState, endState, [1, 0, 0])
				energy = self._aStarCostFunction(startState, endState, [0, 0, 1])
				time = self._aStarCostFunction(startState, endState, [1, 0, 1])

				row += [distance, energy, time]
				
			if (i == (len(path)-1)) or (path[i] != path[i+1]):
				sequence += [row]
				
		sequence = [['isStation', 'x', 'y', 'z', 'distanceMeters', 'energyJoules', 'timeSeconds']] + sequence
		
		return sequence
	
class aStarSearchNode:
	'''
	This class is used to assist in the A* search
	'''
	def __init__(self,state,parent,cost = 0):
		self.state = state
		self.parent = parent
		self.cost = cost
		
	def getPath(self):
		path = [self.state]
		node = self
		while node.parent:
			path.append(node.parent.state)
			node = node.parent
			
		path.reverse()
		return path

class FieldDStarNode:
	'''
	This class is intended to assist for the DStar search mode.
	This takes longer but is much more flexible, as it allows
	for more than the 8 cardinal directions.
	'''
	def __init__(self,coordinates,cost = float('inf'),rhs = float('inf')):
		self.coordinates = coordinates
		self.cost = cost
		self.rhs = rhs # default set to positive infinity