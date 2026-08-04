"""map-geo MCP server: 地理坐标与距离计算。

提供 3 个工具:

- ``geocode(address)``                          地名 → 经纬度
- ``distance_matrix(origins, destinations)``    批量两点距离矩阵(km / 公路估计)
- ``nearby_pois(lat, lng, type, radius_km)``    附近 POI 检索

数据策略:
- 仓库自带本地 POI 数据库,覆盖湖北 17 地市的核心 POI(行政中心、工业园、医院、学校、机场、火车站、大型水体等)。
- 距离计算用 **Haversine 公式**,纯标准库实现。
- 公路距离 = Haversine × 1.25 经验系数(覆盖弯道)。
- 真实路网距离 / 实时 POI 检索仍需接高德 / 百度地图 API,业务方申请 key 后通过环境变量 ``LVKE_MAPGEO_AMAP_KEY`` 启用(暂留扩展位)。
"""
