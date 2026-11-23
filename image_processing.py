"""用户选择四角的页面透视校正，不改变原始保存图片。"""
import cv2
import numpy as np
from ocr_engine import order_quad


def rectify_page(image, points):
    quad = order_quad(points)
    if len(set(map(tuple, quad))) != 4 or not cv2.isContourConvex(quad) or cv2.contourArea(quad) < 16:
        raise ValueError('四角必须是四个不同且围成凸四边形的点，请重新选择')
    width = max(2, round(max(np.linalg.norm(quad[0]-quad[1]), np.linalg.norm(quad[3]-quad[2]))))
    height = max(2, round(max(np.linalg.norm(quad[0]-quad[3]), np.linalg.norm(quad[1]-quad[2]))))
    target = np.float32([[0, 0], [width-1, 0], [width-1, height-1], [0, height-1]])
    transform = cv2.getPerspectiveTransform(quad, target)
    return cv2.warpPerspective(image, transform, (width, height), borderMode=cv2.BORDER_REPLICATE)
