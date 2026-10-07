from spine_labels import label_map_for_values


def test_native_spineps_labels_are_named_and_visible():
    result = label_map_for_values([0, 41, 49, 52, 53, 70, 74, 80, 100], {1: ('Body', (1, 2, 3, 160))})
    assert set(result) == {41, 49, 52, 53, 70, 74, 80, 100}
    assert result[41][0] == 'Vertebral arch'
    assert result[100][0] == 'Intervertebral disc'
    assert all(color[3] > 0 for _, color in result.values())


def test_unknown_foreground_labels_are_not_hidden():
    assert label_map_for_values([120], {})[120][0] == 'Structure 120'


def test_legacy_labels_keep_existing_names_and_colors():
    legacy = {1: ('Body', (255, 55, 55, 160)), 2: ('Pedicles', (30, 130, 255, 160))}
    assert label_map_for_values([0, 1, 2], legacy) == legacy
