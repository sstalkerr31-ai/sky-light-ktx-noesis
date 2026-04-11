# sky-light-ktx-noesis
Python script for Noesis to view custom KTX textures from Sky: COTL (Work in Progress)
# Sky: COTL KTX Texture Plugin for Noesis 🕯️

A specialized Python script for **Noesis** designed to parse and view custom `.ktx` texture files from the game *Sky: Children of the Light*.

> **Status:** Work in Progress (WIP) / Experimental 🛠️
> 
> Currently, the script is in the early stages of development. It can access the data, but image alignment and format detection (ASTC/BC7) are being refined.

## 🔍 Features
- Custom header offset handling for Sky-specific KTX files.
- Basic support for mobile (ASTC) and PC (BC7) texture structures.
- Open Source (MIT License).

## 🛠️ Requirements
- [Noesis](https://richwhitehouse.com) by Rich Whitehouse.
- Python 3.x (Integrated into Noesis).

## 📥 Installation
1. Download the `fmt_Sky_Kram.py` file.
2. Place it into your Noesis plugins folder: `Noesis/plugins/python`.
3. Restart Noesis.

## 🚧 Known Issues & "The Twist"
The KTX files in Sky: COTL use a non-standard header. 
- **Current Issue:** Image output may appear as "noise" or have diagonal artifacts due to incorrect stride/alignment.
- **Goal:** Correctly parse the `0x80` offset and automate ASTC block size detection.

## 🤝 Contributing
I am an aspiring developer (14 years old) from Russia, passionate about reverse engineering and **Sky: Children of the Light**. Since this project is Open Source, feel free to submit a **Pull Request** or open an **Issue** if you know how to fix the data alignment!

## 📜 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---
*Disclaimer: This tool is for educational and research purposes only.*
