"""Flattened top-cell export copy, per-layer union and optional CAD representation."""
import shapely

def prepare(snapshot,options,progress):
    layers=[];source_count=0;merged_count=0
    keys=sorted(snapshot.layers,key=lambda key:tuple(map(int,key.split('/'))))
    for j,key in enumerate(keys):
        polygons=[]
        for raw in snapshot.layers[key]:
            geometry=shapely.from_wkb(raw)
            if not geometry.is_valid:raise ValueError(f'图层 {key} 含无效输出几何')
            for polygon in shapely.get_parts(geometry):
                if polygon.is_empty:continue
                if polygon.geom_type!='Polygon':raise ValueError(f'图层 {key} 含非面输出几何')
                polygons.append(polygon)
        source_count+=len(polygons)
        if options.merge_layers and len(polygons)>1:
            # Bound the temporary native union size; all work is on copies.
            chunks=[]
            for start in range(0,len(polygons),256):
                chunks.append(shapely.union_all(polygons[start:start+256]))
                progress(round(12*(j+min(1,(start+256)/len(polygons)))/max(1,len(keys))),f'Merge 图层 {key}')
            polygons=list(shapely.get_parts(shapely.union_all(chunks)))
        merged_count+=len(polygons)
        name='L'+key.replace('/','_D')
        for polygon in polygons:
            if options.scale!=1:
                polygon=shapely.transform(polygon,lambda xy:xy*options.scale)
            if options.separate_holes:
                layers.append((name,shapely.Polygon(polygon.exterior)))
                layers.extend((name+'_holes',shapely.Polygon(ring)) for ring in polygon.interiors)
            else:layers.append((name,polygon))
        progress(round(12*(j+1)/max(1,len(keys))),f'准备展开图层 {key}')
    return layers,source_count,merged_count
