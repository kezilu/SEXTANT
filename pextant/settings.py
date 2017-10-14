import os
import numpy as np
from pextant.EnvironmentalModel import GDALMesh
from pextant.analysis.loadWaypoints import JSONloader
from pextant.explorers import Astronaut
from pextant.mesh.MeshVisualizer import MeshVizM
from pextant.solvers.astarMesh import astarSolver, ExplorerCost

explorer = Astronaut(80)
#mv = MeshViz(True)
mp = MeshVizM()
DATA_ROOT = os.path.join('../..','data')
MAPS_ROOT = os.path.join(DATA_ROOT,'maps')
WAYPOINTS_ROOT = os.path.join(DATA_ROOT,'waypoints')
AMES_DEM = os.path.join(MAPS_ROOT, 'Ames', 'Ames.tif')
HI_DEM_PATH = os.path.join(MAPS_ROOT,'test5-DEM.tif')
HI_DEM_LOWQUAL_PATH = os.path.join(MAPS_ROOT,'HI_lowqual_DEM.tif')
HI_DEM_AERIAL_PATH = os.path.join(MAPS_ROOT,'dem','HI_air_imagery.tif')
HI_DEM = GDALMesh(HI_DEM_PATH)
HI_DEM_LOWQUAL = GDALMesh(HI_DEM_LOWQUAL_PATH)
HI_DEM_AERIAL = GDALMesh(HI_DEM_AERIAL_PATH)
ID_DEM_PATH = os.path.join(MAPS_ROOT, 'Idaho', 'hwmidres.tif')
ID_DEM_HIGHRES = os.path.join(MAPS_ROOT, 'Idaho', 'hwfull.tif')
DEM_ID = {
    20: GDALMesh(ID_DEM_PATH),
    2: GDALMesh(ID_DEM_HIGHRES)
}
DEM_AMES = {
    50: GDALMesh(AMES_DEM)
}
TEST_JSON = os.path.join(WAYPOINTS_ROOT, 'HI_13Nov16_MD7_A.json')
MD_HI = dict()
WP_HI = dict()
for i in range(2,10):
    MD_HI[i] = os.path.join(WAYPOINTS_ROOT, ('HI_%02dNov16_MD%s_A.json')%(i+6,i))
    WP_HI[i] = JSONloader.from_file(MD_HI[i])
MD_ID = {10: os.path.join(WAYPOINTS_ROOT, 'MD10_EVA10_Stn18_Stn23_X.json')}
WP_ID = {10: JSONloader.from_file(MD_ID[10])}