# import os
import requests
# import numpy as np
import pandas as pd
from bs4 import BeautifulSoup
from typing import List, Optional, Dict, Union


### EAPLE API documents url:
### https://api.eagle.cool/






# /api/item/info
# /api/item/thumbnail
# /api/item/list   //////用這個filter出folder的問題  //////OPK


############################################# 操作資料夾相關 #############################################

# 通用請求函數
def send_request_to_eagle(endpoint: str, method: str = "GET", payload: dict = None) -> Dict[str, Union[bool, Dict, str]]:
    url = f"http://localhost:41595/api/{endpoint}"
    try:
        if method == "GET":
            response = requests.get(url, params=payload)
        elif method == "POST":
            response = requests.post(url, json=payload)
        response.raise_for_status()
        return response.json()     # {"status": "success", "data": }
    except requests.RequestException as e:
        return {"status": "error", "data": str(e)}  # 保持與 API 返回結構一致
    
# 資料夾相關操作
def EAGLE_get_folders():
    """
    獲取所有資料夾列表。
    """
    return send_request_to_eagle("folder/list")

def EAGLE_get_recent_folders():
    """
    獲取最近使用的資料夾列表。
    """
    return send_request_to_eagle("folder/listRecent")

def EAGLE_get_folders_df() -> pd.DataFrame:
    """
    获取 Eagle 文件夹列表，并将其转换为 Pandas DataFrame。

    Returns:
        pd.DataFrame: 包含文件夹信息的 DataFrame，如果请求失败，则返回空 DataFrame。
    """
    response = EAGLE_get_folders()
    if response.get("status") != "success":
        print(f"Error fetching folders: {response.get('data')}")
        return pd.DataFrame()  # 返回空的 DataFrame

    # 提取文件夹数据
    folders_data = response.get("data", [])
    if not folders_data:
        print("No folder data found.")
        return pd.DataFrame()

    # 转换为 DataFrame
    df = pd.DataFrame(folders_data)
    return df

def EAGLE_get_folders_df_all(flatten: bool = True) -> pd.DataFrame:
    """
    获取 Eagle 文件夹列表，并将其转换为 Pandas DataFrame。
    如果 flatten=True，會遞迴展開所有 children folders 合併成一張表。

    Returns:
        pd.DataFrame: 包含所有資料夾（包括子資料夾）的資訊。
    """
    response = EAGLE_get_folders()
    if response.get("status") != "success":
        print(f"Error fetching folders: {response.get('data')}")
        return pd.DataFrame()

    folders_data = response.get("data", [])
    if not folders_data:
        print("No folder data found.")
        return pd.DataFrame()

    all_folders = []

    def extract_folder_info(folder, parent_name=""):
        # 加入當前資料夾
        info = folder.copy()
        info["parentName"] = parent_name
        info.pop("children", None)  # 暫時移除 children，避免 DataFrame 爆掉
        all_folders.append(info)

        # 遞迴加入 children 資訊
        for child in folder.get("children", []):
            extract_folder_info(child, parent_name=folder.get("name"))

    for folder in folders_data:
        extract_folder_info(folder)

    return pd.DataFrame(all_folders)

def EAGLE_create_folder(folderName: str): #tags: Optional[List[str]] = None
    """
    https://api.eagle.cool/folder/create
    創建新的資料夾。

    tags目前創建失敗
    parent id還沒弄
    """
    payload = {"folderName": folderName} # , "tags": tags
    return send_request_to_eagle("folder/create", "POST", payload)


def EAGLE_update_folder_name(folderId: str, newName: str):
    """
    重命名資料夾。
    """
    payload = {"folderId": folderId, "newName": newName}
    return send_request_to_eagle("folder/rename", "POST", payload)



def EAGLE_update_folder_details(folderId: str, 
                                newName: Optional[str] = None, 
                                newDescription: Optional[str] = None, 
                                newColor: Optional[str] = None):
    """
    更新資料夾屬性（名稱、描述、顏色）。

    var data = {
    "folderId": "KMUFMKTBHINM4",
    "newName": "New Name",
    "newDescription": "New Description",
    "newColor": "red"
    };

    """
    payload = {"folderId": folderId}
    if newName:
        payload["newName"] = newName
    if newDescription:
        payload["newDescription"] = newDescription
    if newColor:
        payload["newColor"] = newColor
    return send_request_to_eagle("folder/update", "POST", payload)


############################################# query圖片用 #############################################

# EAGLE_get_img_list(): ####去jupyter notebook找

############################################# 下載圖片用 #############################################

def EAGLE_add_image_from_url(url: str, folderId: str, name: Optional[str] = None, 
                             website: Optional[str] = None, tags: Optional[List[str]] = None):
    """
    從 URL 新增單張圖片。

    ### 這個跟下面一次新增多個的函數是一起的，想好怎麼設計
    ### 注意重複時目前得手動更改
    """

    # data = {
    #     "url": "http://imgcdn.atyun.com/2018/09/1_7PdM6h3jL3lDRS1b1CR9Wg.png",
    #     "folderId": 'LDUADJJ4IBNUA',
    #     "name": "test_dsfsdfsdfg",
    #     "website": "https://test123/default",
    #     "tags": ["Illustration", "Design"],
    #     # "headers": {XDD} #爬蟲用
    # }
    payload = {"url": url, "folderId": folderId, "name": name, "website": website, "tags": tags}
    return send_request_to_eagle("item/addFromURL", "POST", payload)



def EAGLE_add_img_from_json(payload):
    return send_request_to_eagle("item/addFromURL", "POST", payload)


# EAGLE_add_images_from_urls
def EAGLE_add_multiple_img_from_json(payload):
    ### 舊版，沒使用GPT
    ### 注意重複時目前得手動更改

    ### download multiple images example
    # data = {
    #   "items": [
    #       {
    #           "url": "http://imgcdn.atyun.com/2018/09/0_7i8JA5t1Nx3HlK4E-e1536217892333.jpg",
    # #           "name": "Work", #不命名就會用原始檔名
    # #           "website": "https://dribbble.com/shots/12020761-Work", #可命可不命
    # #           "tags": ["Illustration", "Design"],
    #       },
    #       {
    #           "url": "http://imgcdn.atyun.com/2018/09/1_EM8x5jAL-SeUUG7b4anCQg.gif",
    # #           "name": "Work2", #不命名就會用原始檔名
    # #           "website": "https://dribbble.com/shots/12061400-work2", #可命可不命
    # #           "tags": ["Illustration", "Design"],
    #       }
    #   ],
    #   "folderId": 'LDUCBHJTFP952',
    # }
    # EAGLE_add_multiple_img_from_json(data)

    return send_request_to_eagle("item/addFromURLs", "POST", payload)


def EAGLE_add_bookmark(url: str, name: str, tags: Optional[List[str]] = None):
    """
    新增書籤。
    ### 注意項目重複時目前得手動更改
    
    ### add_bookmark example
    EG.EAGLE_add_bookmark(
        url = "https://www.pixiv.net/artworks/83585181",
        name = "アルトリア･キャスター",
        tags = ['aa','bb_test']
    )    
    """

    payload = {"url": url, "name": name, "tags": tags}
    return send_request_to_eagle("item/addBookmark", "POST", payload)


############################################# 雜 #############################################
def EAGLE_get_tags():
    """
    獲取 Eagle 庫中所有標籤。
    """
    return send_request_to_eagle("tag/list")

def EAGLE_get_item_info(item_id: str):
    """
    取得指定項目的詳細資訊。
    """
    response = send_request_to_eagle("item/info", "GET", {"itemId": item_id})
    if response.get("status") == "success":
        return response

    # 某些版本的 API 可能使用 id 參數。
    return send_request_to_eagle("item/info", "GET", {"id": item_id})


def EAGLE_get_application_info():
    """
    獲取 Eagle App 應用資訊。
    """
    return send_request_to_eagle("application/info")

def EAGLE_get_library_info():
    """
    獲取當前運行的 Eagle 資源庫的詳細信息。

    Returns:
        dict: 包含資源庫詳細信息的字典。如果請求失敗，返回錯誤信息。
    """
    response = send_request_to_eagle("library/info", "GET")
    if response.get("status") == "error":
        return {"status": "error", "data": response.get("data")}
    return response

def EAGLE_get_current_library_path() -> str:
    """
    获取当前 Eagle 资源库的路径。

    Returns:
        str: 当前资源库的路径。如果请求失败或路径未找到，则返回错误信息。
    """
    response = send_request_to_eagle("library/info", "GET")
    if response.get("status") != "success":
        raise ValueError(f"Failed to fetch library info: {response.get('data')}")

    # 提取资源库路径
    library_path = response.get("data", {}).get("library", {}).get("path")
    if not library_path:
        raise ValueError("Library path not found in response.")
    
    return library_path

def EAGLE_update_item_tags(itemId: str, tags: List[str]):
    """                 
    未驗證
    更新指定圖片的標籤。

    Args:
        itemId (str): 圖片的 ID。
        tags (List[str]): 新的標籤列表。

    Returns:
        dict: 包含更新操作結果的字典。
    """
    payload = {"itemId": itemId, "tags": tags}
    return send_request_to_eagle("item/update", "POST", payload)



def EAGLE_list_items(limit: int = 200, offset: int = 0, orderBy: Optional[str] = None, 
                     keyword: Optional[str] = None, ext: Optional[str] = None, ### reverse: bool = False,(reverse目前無法使用，API有bug)
                     tags: Optional[List[str]] = None, folders: Optional[List[str]] = None):
    """
    重要的搜尋功能
    使用 /api/item/list 獲取圖片列表。

    Args:
        limit (int): 返回的最大項目數量，默認為 200。
        offset (int): 偏移量，用於分頁，默認為 0。
        orderBy (Optional[str]): 排序方式，可用值：CREATEDATE, FILESIZE, NAME, RESOLUTION。
        reverse (bool): 是否按降序排序，默认为 False（升序）。
        keyword (Optional[str]): 按關鍵字過濾。
        ext (Optional[str]): 按文件擴展名過濾，如 jpg, png。
        tags (Optional[List[str]]): 按標籤過濾，列表中的標籤以逗號分隔。
        folders (Optional[List[str]]): 按資料夾過濾，資料夾 ID 列表以逗號分隔。

    Returns:
        dict: 包含請求結果的字典。

    """
    
    ### 目前reverse功能壞掉
    # # 构建排序参数，添加 "-" 表示降序
    # if orderBy and reverse:
    #     print('reverse true----------')
    #     orderBy = f"-{orderBy}"

    # 构建请求参数
    payload = {
        "limit": limit,
        "offset": offset,
        "orderBy": orderBy,
        "keyword": keyword,
        "ext": ext,
        "tags": tags,
        "folders": ",".join(folders) if folders else None,
    }
    # 过滤掉值为 None 的键
    payload = {k: v for k, v in payload.items() if v is not None}

    # 使用通用请求函数
    return send_request_to_eagle("item/list", "GET", payload)


##### 之後再做
# def EAGLE_add_items_from_path(filePaths: List[str], folderId: str):
#     """
#     批量將本地文件導入到指定資料夾。

#     Args:
#         filePaths (List[str]): 文件的絕對路徑列表。
#         folderId (str): 資料夾的 ID。

#     Returns:
#         dict: 包含導入操作結果的字典。
#     """
#     payload = {"paths": filePaths, "folderId": folderId}
#     try:
#         response = requests.post('http://localhost:41595/api/item/addFromPath', json=payload)
#         response.raise_for_status()
#         return response.json()
#     except requests.RequestException as e:
#         return {"status": "error", "data": str(e)}

if __name__ == "__main__":
    print('This is EAGPE_api')
