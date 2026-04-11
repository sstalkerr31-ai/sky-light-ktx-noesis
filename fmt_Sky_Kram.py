from inc_noesis import *

def registerNoesisTypes():
    handle = noesis.register("Sky Children of the Light", ".ktx")
    noesis.setHandlerTypeCheck(handle, lambda data: 1)
    noesis.setHandlerLoadRGBA(handle, skyLoadRGBA)
    return 1

def skyLoadRGBA(data, texList):
    width = int.from_bytes(data[36:40], byteorder='little')
    height = int.from_bytes(data[40:44], byteorder='little')
    
    # Смещение, которое мы нашли в HxD
    offset = 0xB0
    needed = width * height # 262144 для 512x512
    
    # САМЫЙ ВАЖНЫЙ МОМЕНТ: делаем жесткую копию в bytearray
    # Это лечит ошибку "0 vs 262144"
    try:
        pixelData = bytearray(data[offset : offset + needed])
        print("Log: Buffer captured, size: " + str(len(pixelData)))
        
        # 1. Пробуем декодировать BC7 (ID 14) силами rapi
        # Это самый стабильный метод
        rgba = rapi.imageDecodeBC7(pixelData, width, height)
        if rgba:
            tex = NoeTexture("Sky_PC_BC7", width, height, rgba, noesis.NOESISTEX_RGBA32)
            texList.append(tex)
            print("Log: Success BC7!")
            return 1

        # 2. Если BC7 не пошел, пробуем ASTC 4x4 (мобильный формат)
        rgba = rapi.imageDecodeASTC(pixelData, width, height, 4, 4, 0)
        if rgba:
            tex = NoeTexture("Sky_Mobile_ASTC", width, height, rgba, noesis.NOESISTEX_RGBA32)
            texList.append(tex)
            print("Log: Success ASTC!")
            return 1

    except Exception as e:
        print("Log: Error: " + str(e))

    # 3. ПОСЛЕДНИЙ ШАНС (RAW VIEW)
    # Если декодеры не тянут, просто показываем байты как есть
    # Дополняем до RGBA32 (нужно 4 байта на пиксель), чтобы Noesis не ругался
    fake_rgba = pixelData + bytearray(width * height * 3) 
    tex = NoeTexture("Sky_RAW_Diagnostic", width, height, fake_rgba, noesis.NOESISTEX_RGBA32)
    texList.append(tex)
    
    return 1
