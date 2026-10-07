import colorsys

SPINEPS_NAMES = {
    40: 'Full vertebra', 41: 'Vertebral arch', 42: 'Spinous process',
    43: 'Left transverse process', 44: 'Right transverse process',
    45: 'Left superior articular process', 46: 'Right superior articular process',
    47: 'Left inferior articular process', 48: 'Right inferior articular process',
    49: 'Vertebral body border', 50: 'Vertebral body', 51: 'Dens',
    52: 'Superior endplate', 53: 'Inferior endplate',
    58: 'Superior disc', 59: 'Inferior disc', 60: 'Spinal cord',
    61: 'Spinal canal', 62: 'Endplate', 63: 'Left rib', 64: 'Right rib',
    70: 'Left sacral ala', 71: 'Right sacral ala', 72: 'Posterior sacral elements',
    73: 'Sacral body', 74: 'Sacral endplate', 80: 'Metal', 100: 'Intervertebral disc',
}


def label_map_for_values(values, legacy):
    values = sorted(int(value) for value in values if value > 0)
    if all(value in legacy for value in values):
        return dict(legacy)
    result = {}
    for index, value in enumerate(values):
        rgb = colorsys.hsv_to_rgb((index * 0.61803398875) % 1, 0.7, 1)
        result[value] = (SPINEPS_NAMES.get(value, f'Structure {value}'),
                         (*[round(channel * 255) for channel in rgb], 160))
    return result
