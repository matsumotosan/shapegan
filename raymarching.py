import torch
import numpy as np
import random
from tqdm import tqdm
from PIL import Image

from model import SDFNet, LATENT_CODES_FILENAME
from util import device
from scipy.spatial.transform import Rotation

sdf_net = SDFNet()
sdf_net.load()
sdf_net.eval()
latent_codes = torch.load(LATENT_CODES_FILENAME).to(device)

def get_rotation_matrix(angle, axis='y'):
    rotation = Rotation.from_euler(axis, angle, degrees=True)
    matrix = np.identity(4)
    matrix[:3, :3] = rotation.as_dcm()
    return matrix

def get_camera_transform(camera_distance, rotation_y, rotation_x):
    camera_pose = np.identity(4)
    camera_pose[2, 3] = -camera_distance
    camera_pose = np.matmul(camera_pose, get_rotation_matrix(rotation_x, axis='x'))
    camera_pose = np.matmul(camera_pose, get_rotation_matrix(rotation_y, axis='y'))

    return camera_pose


def get_image(latent_code, camera_position, light_position, resolution = 512, focal_distance = 1.6, threshold = 0.0001, iterations=1000):
    camera_forward = camera_position / np.linalg.norm(camera_position) * -1
    camera_distance = np.linalg.norm(camera_position).item()
    up = np.array([0, 1, 0])
    camera_right = np.cross(camera_forward, up)
    camera_up = np.cross(camera_forward, camera_right)
    
    screenspace_points = np.meshgrid(
        np.linspace(-1, 1, resolution),
        np.linspace(-1, 1, resolution),
    )
    screenspace_points = np.stack(screenspace_points)
    screenspace_points = screenspace_points.reshape(2, -1).transpose()
    
    points = np.tile(camera_position, (screenspace_points.shape[0], 1))
    points = points.astype(np.float32)
    
    ray_directions = screenspace_points[:, 0] * camera_right[:, np.newaxis] \
        + screenspace_points[:, 1] * camera_up[:, np.newaxis] \
        + focal_distance * camera_forward[:, np.newaxis]
    ray_directions = ray_directions.transpose().astype(np.float32)
    ray_directions /= np.linalg.norm(ray_directions, axis=1)[:, np.newaxis]

    points += ray_directions * (camera_distance - 1.5)

    indices = np.arange(points.shape[0])

    model_pixels = np.zeros(points.shape[0], dtype=bool)

    latent_codes = latent_code.repeat(indices.shape[0], 1)

    for i in tqdm(range(iterations)):
        test_points = torch.tensor(points[indices, :]).to(device)
        with torch.no_grad():
            sdf = sdf_net.forward(test_points, latent_codes[:indices.shape[0], :]).detach().cpu().numpy()
        sdf = np.clip(sdf, -0.1, 0.1)
        points[indices, :] += ray_directions[indices, :] * sdf[:, np.newaxis]
        
        hits = (sdf > 0) & (sdf < threshold)
        model_pixels[indices[hits]] = 1
        indices = indices[~hits]
        
        misses = np.linalg.norm(points[indices, :] - camera_position[np.newaxis, :], axis=1) > camera_distance + 1.5
        indices = indices[~misses]
        
        if indices.shape[0] < 2:
            break
    
    model_pixels[indices] = 1
    model_points = points[model_pixels]

    normal = sdf_net.get_normals(latent_code, torch.tensor(model_points, device=device)).detach().cpu().numpy()
    
    light_direction = light_position[np.newaxis, :] - model_points
    light_direction /= np.linalg.norm(light_direction, axis=1)[:, np.newaxis]

    diffuse = np.einsum('ij,ij->i', light_direction, normal)
    diffuse = np.clip(diffuse, 0, 1)

    reflect = light_direction - np.einsum('ij,ij->i', light_direction, normal)[:, np.newaxis] * normal * 2
    reflect /= np.linalg.norm(reflect, axis=1)[:, np.newaxis]
    reflect *= -1
    specular = np.einsum('ij,ij->i', reflect, ray_directions[model_pixels, :])
    specular = np.clip(specular, 0.0, 1.0)
    specular = np.power(specular, 0.5)

    color = np.array([0.8, 0.1, 0.1])[np.newaxis, :] * (diffuse * 0.5 + 0.5)[:, np.newaxis]
    color += (specular * 0.3)[:, np.newaxis]

    pixels = np.ones((points.shape[0], 3))
    pixels[model_pixels] = color
    pixels = pixels.reshape((resolution, resolution, 3))    
    return pixels
    
   
codes = list(range(latent_codes.shape[0]))
random.shuffle(codes)

for i in [182]:
    print(i)
    camera_pose = get_camera_transform(2.2, 147, 20)
    camera_position = np.matmul(np.linalg.inv(camera_pose), np.array([0, 0, 0, 1]))[:3]
    light_matrix = get_camera_transform(6, 164, 50)
    light_position = np.matmul(np.linalg.inv(light_matrix), np.array([0, 0, 0, 1]))[:3]
    pixels = get_image(latent_codes[i], camera_position, light_position)
    
    img = Image.fromarray(np.uint8(pixels * 255) , 'RGB')
    img.save('screenshots/raymarching.png')
    img.show()