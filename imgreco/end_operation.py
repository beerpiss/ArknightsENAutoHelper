from dataclasses import dataclass
from dataclasses_json import dataclass_json
import sys
import math

import cv2
import numpy as np
from imgreco import ocr
from util import cvimage as Image

from util.richlog import get_logger
from . import imgops
from . import item
from . import minireco
from . import resources
from . import common

logger = get_logger(__name__)


class RecognizeSession:
    def __init__(self):
        self.recognized_groups = []
        self.low_confidence = False
        self.vh = 0
        self.vw = 0
        self.learn_unrecognized = False


def tell_stars(starsimg):
    thstars = np.asarray(starsimg.convert("L")) > 96
    width, height = thstars.shape[::-1]
    starwidth = width // 3
    threshold = height * (width / 12)
    stars = []
    star1 = thstars[:, 0:starwidth]
    stars.append(np.count_nonzero(star1) > threshold)

    star2 = thstars[:, starwidth : starwidth * 2]
    stars.append(np.count_nonzero(star2) > threshold)

    star3 = thstars[:, starwidth * 2 :]
    stars.append(np.count_nonzero(star3) > threshold)
    return tuple(stars)


recozh = minireco.MiniRecognizer(
    resources.load_pickle("minireco/NuberNext-DemiBoldCondensed.dat")
)


def tell_group(
    groupimg,
    session,
    bartop,
    barbottom,
):
    logger.logimage(groupimg)
    grouptext = groupimg.crop((0, barbottom, groupimg.width, groupimg.height))

    thim = imgops.enhance_contrast(grouptext.convert("L"), 60)
    # thim = imgops.crop_blackedge(thim)
    logger.logimage(thim)

    groupname, diff = tell_group_name_alt(thim, session)
    if diff > 0.8:
        session.low_confidence = True

    if groupname == "Lucky Drops":
        return (
            groupname,
            [
                item.RecognizedItem(
                    item_id="furni", name="(Furniture)", quantity=1, item_type="FURN"
                )
            ],
        )

    vw, vh = session.vw, session.vh
    itemwidth = 20.370 * vh
    itemcount = roundint(groupimg.width / itemwidth)
    logger.logtext("group has %d items" % itemcount)
    result = []
    for i in range(itemcount):
        itemimg = groupimg.crop(
            (itemwidth * i, 0.000 * vh, itemwidth * (i + 1), 18.981 * vh)
        )
        # x1, _, x2, _ = (0.093*vh, 0.000*vh, 19.074*vh, 18.981*vh)
        itemimg = itemimg.crop((0.093 * vh, 0, 19.074 * vh, itemimg.height))
        recognized_item = item.tell_item(
            itemimg, with_quantity=True, learn_unrecognized=session.learn_unrecognized
        )
        if recognized_item.low_confidence:
            session.low_confidence = True
        result.append(recognized_item)
    return (groupname, result)


def tell_group_ep10(
    groupimg,
    session,
    bartop,
    barbottom,
):
    logger.logimage(groupimg)
    grouptext = groupimg.subview((0, barbottom, groupimg.width, groupimg.height))

    thim = imgops.enhance_contrast(grouptext.convert("L"), 60)
    # thim = imgops.crop_blackedge(thim)
    logger.logimage(thim)

    groupname, diff = tell_group_name_ocr(thim, session)
    if diff > 0.6:
        session.low_confidence = True

    if groupname == "Lucky Drops":
        return (
            groupname,
            [
                item.RecognizedItem(
                    item_id="furni", name="(Furniture)", quantity=1, item_type="FURN"
                )
            ],
        )

    vw, vh = session.vw, session.vh
    itemwidth = 19.167 * vh
    itemcount = roundint(groupimg.width / itemwidth)
    logger.logtext("group has %d items" % itemcount)
    result = []
    for i in range(itemcount):
        itemimg = groupimg.subview(
            (itemwidth * i, 0.000 * vh, itemwidth * (i + 1), bartop)
        )
        # x1, _, x2, _ = (0.093*vh, 0.000*vh, 19.074*vh, 18.981*vh)
        center_x = itemimg.width / 2
        center_y = itemimg.height / 2
        itembox_radius = 16.492 * vh / 2
        itemimg = itemimg.crop(
            (
                center_x - itembox_radius,
                center_y - itembox_radius,
                center_x + itembox_radius + 1,
                center_y + itembox_radius + 1,
            )
        )
        recognized_item = item.tell_item(
            itemimg, with_quantity=True, learn_unrecognized=session.learn_unrecognized
        )
        if recognized_item.low_confidence:
            session.low_confidence = True
        result.append(recognized_item)
    return (groupname, result)


def tell_group_name_alt(img, session):
    names = [
        ("LMD", "EXP & LMD"),
        ("Regular", "Regular Drops"),
        ("Special", "Special Drops"),
        ("Lucky", "Lucky Drops"),
        ("Extra", "Extra Drops"),
        ("First Clear", "First Clear"),
        ("Refund", "Sanity refunded"),
    ]
    comparsions = []
    scale = session.vh * 100 / 1080

    for name, group in names:
        if group in session.recognized_groups:
            continue
        template = resources.load_image_cached(f"end_operation/group/{name}.png", "L")
        scaled_height = template.height * scale
        scaled_height_floor = math.floor(scaled_height)
        scaled_template_floor = imgops.scale_to_height(template, scaled_height_floor)
        if (
            scaled_template_floor.width > img.width
            or scaled_template_floor.height > img.height
        ):
            raise ValueError("image smaller than template")
        _, diff_floor = imgops.match_template(
            img, scaled_template_floor, cv2.TM_SQDIFF_NORMED
        )
        if scaled_height_floor + 1 <= img.height:
            scaled_template_ceil = imgops.scale_to_height(
                template, scaled_height_floor + 1
            )
            _, diff_ceil = imgops.match_template(
                img, scaled_template_ceil, cv2.TM_SQDIFF_NORMED
            )
            diff = min(diff_floor, diff_ceil)
        else:
            diff = diff_floor
        comparsions.append((group, diff))

    if comparsions:
        comparsions.sort(key=lambda x: x[1])
        logger.logtext(repr(comparsions))
        return comparsions[0]


def tell_group_name_ocr(img, session):

    names = [
        "EXP & LMD",
        "Regular Drops",
        "Special Drops",
        "Lucky Drops",
        "Extra Drops",
        "First Clear",
        "Sanity refunded",
    ]
    all_chars = "".join(set(c for item in names for c in item))
    comparsions = []
    scale = session.vh * 100 / 1080
    from .ocr import acquire_engine_global_cached

    engine = acquire_engine_global_cached("en-us")
    img = imgops.crop_blackedge(img, 1)
    invimg = Image.fromarray(
        cv2.copyMakeBorder(255 - img.array, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255),
        img.mode,
    )
    ocr_result = engine.recognize(invimg, char_whitelist=all_chars)
    logger.logimage(invimg)
    pending = [x for x in names if x not in session.recognized_groups]

    text = ocr_result.text.replace(" ", "")
    match_index, min_distance = ocr.match_distance(text, pending)
    logger.logtext(f"{ocr_result=} {match_index=} {min_distance=}")
    return pending[match_index], min_distance / len(pending[match_index])


def find_jumping(ary, threshold):
    ary = np.array(ary, dtype=np.int16)
    diffs = np.diff(ary)
    shit = [x for x in enumerate(diffs) if abs(x[1]) >= threshold]
    if not shit:
        return []
    groups = [[shit[0]]]
    for x in shit[1:]:
        lastgroup = groups[-1]
        if np.sign(x[1]) == np.sign(lastgroup[-1][1]):
            lastgroup.append(x)
        else:
            groups.append([x])
    logger.logtext(repr(groups))
    pts = []
    for group in groups:
        pts.append(
            int(
                np.average(
                    tuple(x[0] for x in group), weights=tuple(abs(x[1]) for x in group)
                )
            )
            + 1
        )
    return pts


def roundint(x):
    return int(round(x))


# scale = 0


def check_level_up_popup(img):
    vw, vh = common.get_vwvh(img.size)

    lvl_up_img = img.crop(
        (50 * vw - 48.796 * vh, 47.685 * vh, 50 * vw - 23.148 * vh, 56.019 * vh)
    ).convert(
        "L"
    )  # 等级提升
    lvl_up_img = imgops.enhance_contrast(lvl_up_img, 216, 255)
    lvl_up_text = recozh.recognize(lvl_up_img)
    return minireco.check_charseq(lvl_up_text, "Level up")


def check_end_operation(style, friendship, img):
    if style == "interlocking":
        if friendship:
            return check_end_operation_interlocking_friendship(img)
        else:
            raise NotImplementedError()
    else:
        return check_end_operation_ep10(img)


def check_end_operation_legacy_friendship(img):
    vw, vh = common.get_vwvh(img.size)
    template = resources.load_image_cached("end_operation/friendship.png", "RGB")
    operation_end_img = img.crop(
        (117.083 * vh, 64.306 * vh, 121.528 * vh, 69.583 * vh)
    ).convert("RGB")
    mse = imgops.compare_mse(*imgops.uniform_size(template, operation_end_img))
    return mse < 3251


def check_end_operation_legacy(img):
    vw, vh = common.get_vwvh(img.size)
    template = resources.load_image_cached("end_operation/end.png", "L")
    operation_end_img = img.crop(
        (4.722 * vh, 80.278 * vh, 56.389 * vh, 93.889 * vh)
    ).convert("L")
    operation_end_img = imgops.enhance_contrast(operation_end_img, 225, 255)
    mse = imgops.compare_mse(*imgops.uniform_size(template, operation_end_img))
    return mse < 6502


def check_end_operation_ep10(img):
    context = common.ImageRoiMatchingContext(img)
    return bool(
        context.match_roi(
            "end_operation/ep10/rhodes_island", method="mse", threshold=325
        )
    )


def check_end_operation_interlocking_friendship(img):
    vw, vh = common.get_vwvh(img.size)
    return imgops.compare_region_mse(
        img,
        (100 * vw - 34.907 * vh, 55.185 * vh, 100 * vw - 30.556 * vh, 60.370 * vh),
        "end_operation/interlocking/friendship.png",
        logger=logger,
    )


def check_end_operation2(img, threshold=0.8):
    cv_screen = np.asarray(img.convert("L"))
    h, w = cv_screen.shape[:2]
    scale = h / 1080
    if scale != 1:
        cv_screen = cv2.resize(cv_screen, (int(w / scale), 1080))
    template = np.asarray(resources.load_image_cached("end_operation/end2.png", "L"))
    res = cv2.matchTemplate(cv_screen, template, cv2.TM_CCOEFF_NORMED)
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
    return max_val > threshold


def get_end2_rect(img):
    vw, vh = common.get_vwvh(img.size)
    return 38.594 * vw, 88.056 * vh, 61.484 * vw, 95.694 * vh


def get_dismiss_level_up_popup_rect(viewport):
    vw, vh = common.get_vwvh(viewport)
    return (100 * vw - 67.315 * vh, 16.019 * vh, 100 * vw - 5.185 * vh, 71.343 * vh)


get_dismiss_end_operation_rect = get_dismiss_level_up_popup_rect


@dataclass_json
@dataclass
class EndOperationResult:
    operation: str
    stars: list[bool]
    items: list[tuple[str, list[item.RecognizedItem]]]
    low_confidence: bool = False


def recognize(style, im, learn_unrecognized_item=False) -> EndOperationResult:
    if style in {"legacy", "ep10", "sof"}:
        return recognize_ep10(im, learn_unrecognized_item)
    elif style == "interlocking":
        return recognize_interlocking(im, learn_unrecognized_item)
    else:
        raise ValueError(style)


def recognize_legacy(im, learn_unrecognized_item):
    import time

    t0 = time.monotonic()
    vw, vh = common.get_vwvh(im.size)

    lower = im.crop((0, 61.111 * vh, 100 * vw, 100 * vh))
    logger.logimage(lower)

    operation_id = lower.crop((0, 4.444 * vh, 23.611 * vh, 11.388 * vh)).convert("L")
    # logger.logimage(operation_id)
    cv2.threshold(operation_id.array, 180, 255, cv2.THRESH_BINARY, operation_id.array)
    logger.logimage(operation_id)
    from imgreco import stage_ocr

    operation_id_str = stage_ocr.do_tag_ocr(operation_id.array, model_name="chars_end")
    # operation_name = lower.crop((0, 14.074*vh, 23.611*vh, 20*vh)).convert('L')
    # operation_name = imgops.enhance_contrast(imgops.crop_blackedge(operation_name))
    # logger.logimage(operation_name)

    stars = lower.crop((23.611 * vh, 6.759 * vh, 53.241 * vh, 16.944 * vh))
    logger.logimage(stars)
    stars_status = tell_stars(stars)

    # level = lower.crop((63.148 * vh, 4.444 * vh, 73.333 * vh, 8.611 * vh))
    # logger.logimage(level)
    # exp = lower.crop((76.852 * vh, 5.556 * vh, 94.074 * vh, 7.963 * vh))
    # logger.logimage(exp)

    recoresult = EndOperationResult(operation_id_str, stars_status, [], False)

    items = lower.crop((68.241 * vh, 10.926 * vh, lower.width, 35.000 * vh))
    logger.logimage(items)

    x, y = 6.667 * vh, 18.519 * vh
    linedet = items.crop((x, y, x + 1, items.height)).convert("L")
    d = np.asarray(linedet)
    linedet = find_jumping(d.reshape(linedet.height), 55)
    if len(linedet) >= 2:
        linetop, linebottom, *_ = linedet
    else:
        logger.logtext("horizontal line detection failed")
        recoresult.low_confidence = True
        return recoresult
    linetop += y
    linebottom += y

    grouping = items.crop((0, linetop, items.width, linebottom))
    grouping = grouping.resize((grouping.width, 1), Image.BILINEAR)
    grouping = grouping.convert("L")

    logger.logimage(grouping.resize((grouping.width, 16)))

    d = np.array(grouping, dtype=np.int16)[0]
    points = [0, *find_jumping(d, 55)]
    if len(points) % 2 != 0:
        raise RuntimeError("possibly incomplete item list")
    finalgroups = list(zip(*[iter(points)] * 2))  # each_slice(2)
    logger.logtext(repr(finalgroups))

    imggroups = [items.crop((x1, 0, x2, items.height)) for x1, x2 in finalgroups]
    items = []

    session = RecognizeSession()
    session.vw = vw
    session.vh = vh
    session.learn_unrecognized = learn_unrecognized_item

    for group in imggroups:
        groupresult = tell_group(group, session, linetop, linebottom)
        session.recognized_groups.append(groupresult[0])
        items.append(groupresult)

    t1 = time.monotonic()
    if session.low_confidence:
        logger.logtext("LOW CONFIDENCE")
    logger.logtext("time elapsed: %f" % (t1 - t0))
    recoresult.items = items
    recoresult.low_confidence = recoresult.low_confidence or session.low_confidence
    return recoresult


def recognize_ep10(im: Image.Image, learn_unrecognized_item=False):
    import time

    t0 = time.monotonic()
    vw, vh = common.get_vwvh(im.size)

    context = common.ImageRoiMatchingContext(im)

    logger.logimage(im)

    operation_id = im.subview(
        (20.278 * vh, 10.000 * vh, 39.889 * vh, 15.093 * vh)
    ).convert("L")
    # logger.logimage(operation_id)
    cv2.threshold(operation_id.array, 180, 255, cv2.THRESH_BINARY, operation_id.array)
    logger.logimage(operation_id)
    from imgreco import stage_ocr

    operation_id_str = stage_ocr.do_tag_ocr(operation_id.array, model_name="chars_end")
    # operation_name = lower.crop((0, 14.074*vh, 23.611*vh, 20*vh)).convert('L')
    # operation_name = imgops.enhance_contrast(imgops.crop_blackedge(operation_name))
    # logger.logimage(operation_name)

    stars = im.subview((9.907 * vh, 40.926 * vh, 38.056 * vh, 48.333 * vh))
    logger.logimage(stars)
    stars_status = tell_stars(stars)

    # level = lower.crop((63.148 * vh, 4.444 * vh, 73.333 * vh, 8.611 * vh))
    # logger.logimage(level)
    # exp = lower.crop((76.852 * vh, 5.556 * vh, 94.074 * vh, 7.963 * vh))
    # logger.logimage(exp)

    recoresult = EndOperationResult(operation_id_str, stars_status, [], False)

    items = im.crop((7.870 * vh, 71.111 * vh, im.width, 91.481 * vh))
    logger.logimage(items)

    linedet = im.crop((7.870 * vh, 87.222 * vh, im.width, 89.259 * vh)).convert("L")
    xsum = list(np.sum(linedet, axis=1))
    maxsum = max(xsum)
    linetop = next((i for i, x in enumerate(xsum) if x > maxsum * 0.8), 0) + 87.222 * vh
    linebottom = (
        next(
            (len(xsum) - i for i, x in enumerate(reversed(xsum)) if x > maxsum * 0.8), 0
        )
        + 87.222 * vh
    )
    # if len(linedet) >= 2:
    #     linetop, linebottom, *_ = linedet
    # else:
    #     logger.logtext('horizontal line detection failed')
    #     recoresult['low_confidence'] = True
    #     return recoresult
    grouping = im.crop((7.870 * vh, linetop, im.width, linebottom))
    # grouping = grouping.resize((grouping.width, 1), Image.BILINEAR)
    grouping = grouping.convert("L")

    logger.logimage(grouping.resize((grouping.width, 16)))

    d = np.array(grouping, dtype=np.int16)[0]
    points = [0, *find_jumping(d, 55)]
    if len(points) % 2 != 0:
        raise RuntimeError("possibly incomplete item list")
    finalgroups = list(zip(*[iter(points)] * 2))  # each_slice(2)
    logger.logtext(repr(finalgroups))

    imggroups = [items.crop((x1, 0, x2, items.height)) for x1, x2 in finalgroups]
    items = []

    session = RecognizeSession()
    session.vw = vw
    session.vh = vh
    session.learn_unrecognized = learn_unrecognized_item

    for group in imggroups:
        groupresult = tell_group_ep10(
            group, session, linetop - 71.111 * vh, linebottom - 71.111 * vh
        )
        session.recognized_groups.append(groupresult[0])
        items.append(groupresult)

    t1 = time.monotonic()
    if session.low_confidence:
        logger.logtext("LOW CONFIDENCE")
    logger.logtext("time elapsed: %f" % (t1 - t0))
    recoresult.items = items
    recoresult.low_confidence = recoresult.low_confidence or session.low_confidence
    return recoresult


def recognize_interlocking(im):
    import time

    t0 = time.monotonic()
    from imgreco import stage_ocr

    vw, vh = common.get_vwvh(im.size)
    operation_id = im.crop(
        (100 * vw - 26.204 * vh, 21.852 * vh, 100 * vw - 9.907 * vh, 26.204 * vh)
    ).convert("L")
    thr = int(0.833 * vh)
    left, _, _, _ = imgops.cropbox_blackedge2(operation_id, x_threshold=0.833 * vh)
    operation_id = operation_id.crop(
        (left - thr, 0, operation_id.width, operation_id.height)
    )
    cv2.threshold(operation_id.array, 180, 255, cv2.THRESH_BINARY, operation_id.array)
    logger.logimage(operation_id)
    from imgreco import stage_ocr

    operation_id_str = stage_ocr.do_tag_ocr(operation_id.array, model_name="chars_end")

    stars = im.crop(
        (100 * vw - 41.667 * vh, 10.000 * vh, 100 * vw - 11.204 * vh, 20.185 * vh)
    )
    logger.logimage(stars)
    stars_status = tell_stars(stars)

    recoresult = EndOperationResult(operation_id_str, stars_status, [], False)

    items = im.crop((100 * vw - 87.778 * vh, 65.000 * vh, 100 * vw, 89.259 * vh))
    logger.logimage(items)
    sumx = np.asarray(items.convert("RGB")).sum(axis=2).sum(axis=1)
    diffx = np.diff(sumx.astype(np.int32))
    linetop = np.argmax(diffx) + 1
    linebottom = np.argmin(diffx) + 1
    logger.logtext("linetop=%r, linebottom=%r" % (linetop, linebottom))
    grouping = items.crop((0, linetop, items.width, linebottom))
    grouping = grouping.resize((grouping.width, 1), Image.BILINEAR)
    grouping = grouping.convert("L")

    logger.logimage(grouping.resize((grouping.width, 16)))

    d = np.array(grouping, dtype=np.int16)[0]
    points = [0, *find_jumping(d, 55)]
    if len(points) % 2 != 0:
        raise RuntimeError("possibly incomplete item list")
    finalgroups = list(zip(*[iter(points)] * 2))  # each_slice(2)
    logger.logtext(repr(finalgroups))

    imggroups = [items.crop((x1, 0, x2, items.height)) for x1, x2 in finalgroups]
    items = []

    session = RecognizeSession()
    session.vw = vw
    session.vh = vh

    for group in imggroups:
        groupresult = tell_group(group, session, linetop, linebottom)
        session.recognized_groups.append(groupresult[0])
        items.append(groupresult)

    t1 = time.monotonic()
    if session.low_confidence:
        logger.logtext("LOW CONFIDENCE")
    logger.logtext("time elapsed: %f" % (t1 - t0))
    recoresult.items = items
    recoresult.low_confidence = recoresult.low_confidence or session.low_confidence
    return recoresult


def get_still_check_rect(viewport):
    vw, vh = common.get_vwvh(viewport)
    return (7.870 * vh, 71.111 * vh, 100 * vw, 91.481 * vh)


if __name__ == "__main__":
    print(globals()[sys.argv[-2]](Image.open(sys.argv[-1])))
