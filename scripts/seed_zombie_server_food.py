#!/usr/bin/env python3
"""为丧尸服 ARCRealisticSurvival 数据库写入统一进食效果表 consume_items（sgs_farm + 原版）。

v0.3.34 起插件改用单一 consume_items 表（口渴/营养/感染一行配齐），
旧 thirst_items / nutrition_items 仅作迁移来源，不再被运行时读取。
"""
import datetime
import re
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = Path(
    r"C:\Users\DEVIL\Desktop\app\MCBE\Servers\MCBEZombieServer\bedrock_server\plugins\ARCRealisticSurvival\ars_survival.db"
)

# (category, display_name, item_id, tier_price)
SGS_FARM_FOODS = [
    # 原料·农作物
    ("原料·农作物", "小葱", "sgs_farm:onions_spring_crop", 40),
    ("原料·农作物", "玉米", "sgs_farm:maize_crop", 40),
    ("原料·农作物", "白洋葱", "sgs_farm:onions_white_crop", 40),
    ("原料·农作物", "小白菜", "sgs_farm:pak_choi_crop", 50),
    ("原料·农作物", "红洋葱", "sgs_farm:onions_red_crop", 50),
    ("原料·农作物", "绿卷心菜", "sgs_farm:cabbage_green_crop", 50),
    ("原料·农作物", "芜菁", "sgs_farm:turnip_crop", 50),
    ("原料·农作物", "萝卜", "sgs_farm:radish_crop", 50),
    ("原料·农作物", "黄瓜", "sgs_farm:cucumber_crop", 50),
    ("原料·农作物", "夏南瓜", "sgs_farm:summer_squash_crop", 60),
    ("原料·农作物", "夏南瓜", "sgs_farm:zucchini_crop", 60),
    ("原料·农作物", "瑞典芜菁", "sgs_farm:swede_crop", 60),
    ("原料·农作物", "甜玉米", "sgs_farm:sweetcorn_crop", 60),
    ("原料·农作物", "生菜", "sgs_farm:lettuce_crop", 60),
    ("原料·农作物", "稻米", "sgs_farm:rice_crop", 60),
    ("原料·农作物", "茄子", "sgs_farm:eggplant_crop", 60),
    ("原料·农作物", "黑麦", "sgs_farm:rye_crop", 60),
    ("原料·农作物", "山药", "sgs_farm:yam_crop", 70),
    ("原料·农作物", "牛排番茄", "sgs_farm:tomato_beef_crop", 70),
    ("原料·农作物", "红卷心菜", "sgs_farm:cabbage_red_crop", 70),
    ("原料·农作物", "菠菜", "sgs_farm:spinach_crop", 70),
    ("原料·农作物", "冬南瓜", "sgs_farm:butternut_squash_crop", 80),
    ("原料·农作物", "梨子", "sgs_farm:fruit_pear_crop", 80),
    ("原料·农作物", "橙子", "sgs_farm:fruit_orange_crop", 80),
    ("原料·农作物", "水芹", "sgs_farm:cress_crop", 80),
    ("原料·农作物", "青苹果", "sgs_farm:fruit_apple_green_crop", 80),
    ("原料·农作物", "韭葱", "sgs_farm:leeks_crop", 80),
    ("原料·农作物", "欧洲防风草", "sgs_farm:parsnip_crop", 90),
    ("原料·农作物", "粉苹果", "sgs_farm:fruit_apple_pink_crop", 90),
    ("原料·农作物", "西洋菜", "sgs_farm:watercress_crop", 90),
    ("原料·农作物", "青豆", "sgs_farm:beans_green_crop", 90),
    ("原料·农作物", "柠檬", "sgs_farm:fruit_lemon_crop", 100),
    ("原料·农作物", "桃子", "sgs_farm:fruit_peach_crop", 100),
    ("原料·农作物", "樱桃番茄", "sgs_farm:tomato_cherry_crop", 100),
    ("原料·农作物", "甜椒", "sgs_farm:peppers_bell_crop", 100),
    ("原料·农作物", "花椰菜", "sgs_farm:cauliflower_crop", 100),
    ("原料·农作物", "西兰花", "sgs_farm:broccoli_crop", 100),
    ("原料·农作物", "豌豆", "sgs_farm:peas_garden_crop", 100),
    ("原料·农作物", "大蒜", "sgs_farm:garlic_crop", 120),
    ("原料·农作物", "花生", "sgs_farm:peanuts_crop", 120),
    ("原料·农作物", "茴香", "sgs_farm:fennel_crop", 120),
    ("原料·农作物", "菠萝", "sgs_farm:pineapple_crop", 120),
    ("原料·农作物", "辣椒", "sgs_farm:peppers_chili_crop", 120),
    ("原料·农作物", "酸橙", "sgs_farm:fruit_lime_crop", 120),
    ("原料·农作物", "青葡萄", "sgs_farm:grape_green_crop", 120),
    ("原料·农作物", "红葡萄", "sgs_farm:grape_red_crop", 130),
    ("原料·农作物", "金丝瓜", "sgs_farm:melon_canary_crop", 140),
    ("原料·农作物", "大黄", "sgs_farm:rhubarb_crop", 150),
    ("原料·农作物", "哈密瓜", "sgs_farm:melon_cantaloupe_crop", 160),
    ("原料·农作物", "生姜", "sgs_farm:ginger_crop", 160),
    ("原料·农作物", "牛油果", "sgs_farm:fruit_avocado_crop", 180),
    ("原料·农作物", "百香果", "sgs_farm:passionfruit_crop", 180),
    ("原料·农作物", "草莓", "sgs_farm:strawberries_crop", 180),
    ("原料·农作物", "栗子", "sgs_farm:fruit_chestnut_crop", 200),
    ("原料·农作物", "洋蓟", "sgs_farm:artichoke_crop", 220),
    ("原料·农作物", "橄榄", "sgs_farm:fruit_olive_crop", 240),
    ("原料·农作物", "榛子", "sgs_farm:fruit_hazelnut_crop", 260),
    ("原料·农作物", "芦笋", "sgs_farm:asparagus_crop", 280),
    ("原料·农作物", "红加仑", "sgs_farm:redcurrants_crop", 300),
    ("原料·农作物", "黑加仑", "sgs_farm:blackcurrants_crop", 300),
    ("原料·农作物", "醋栗", "sgs_farm:gooseberry_crop", 320),
    ("原料·农作物", "蓝莓", "sgs_farm:blueberries_crop", 350),
    ("原料·农作物", "黑莓", "sgs_farm:blackberries_crop", 380),
    ("原料·农作物", "覆盆子", "sgs_farm:raspberries_crop", 400),
    ("原料·农作物", "樱桃", "sgs_farm:fruit_cherry_crop", 450),
    ("原料·农作物", "芥末", "sgs_farm:wasabi_crop", 800),
    # 原料·香草
    ("原料·香草", "香菜", "sgs_farm:cilantro_crop", 60),
    ("原料·香草", "薄荷", "sgs_farm:mint_crop", 80),
    ("原料·香草", "罗勒", "sgs_farm:basil_crop", 100),
    ("原料·香草", "百里香", "sgs_farm:thyme_crop", 120),
    ("原料·香草", "迷迭香", "sgs_farm:rosemary_crop", 120),
    ("原料·香草", "薰衣草", "sgs_farm:lavender_crop", 140),
    # 原料·加工
    ("原料·加工", "意面粉", "sgs_farm:flour_pasta", 30),
    ("原料·加工", "面包粉", "sgs_farm:flour_bread", 30),
    ("原料·加工", "奶酪轮", "sgs_farm:cheese_wheel", 35),
    ("原料·加工", "玉米粉", "sgs_farm:flour_corn", 40),
    ("原料·加工", "油", "sgs_farm:oil", 65),
    ("原料·加工", "花生粉", "sgs_farm:powder_peanut", 120),
    ("原料·加工", "辣椒粉", "sgs_farm:powder_chili", 120),
    ("原料·加工", "调味酱", "sgs_farm:dressing", 165),
    ("原料·加工", "榛子粉", "sgs_farm:powder_hazelnut", 260),
    # 小吃/半成品
    ("小吃/半成品", "压扁的面包面团", "sgs_farm:dough_bread_flattened", 0),
    ("小吃/半成品", "奶油", "sgs_farm:cream_sweet", 0),
    ("小吃/半成品", "奶油面团", "sgs_farm:dough_brioche", 0),
    ("小吃/半成品", "打发奶油", "sgs_farm:cream_whipped", 0),
    ("小吃/半成品", "水煮蛋", "sgs_farm:egg_boiled", 0),
    ("小吃/半成品", "蛋奶酱", "sgs_farm:custard", 0),
    ("小吃/半成品", "酸奶油", "sgs_farm:cream_sour", 0),
    ("小吃/半成品", "面包屑", "sgs_farm:breadcrumbs", 0),
    ("小吃/半成品", "面包片", "sgs_farm:bread_slice", 0),
    ("小吃/半成品", "面包面团", "sgs_farm:dough_bread", 0),
    ("小吃/半成品", "黄油", "sgs_farm:butter", 0),
    ("小吃/半成品", "芝士酱", "sgs_farm:cheese_sauce", 9),
    ("小吃/半成品", "金丝瓜片", "sgs_farm:melon_slice_canary", 18),
    ("小吃/半成品", "哈密瓜片", "sgs_farm:melon_slice_cantaloupe", 20),
    ("小吃/半成品", "生意面", "sgs_farm:pasta_raw", 30),
    ("小吃/半成品", "鸡腿", "sgs_farm:chicken_drum_raw", 38),
    ("小吃/半成品", "煎蘑菇", "sgs_farm:mushroom_fried", 40),
    ("小吃/半成品", "酥皮碎", "sgs_farm:crumble_top", 45),
    ("小吃/半成品", "薄荷茶", "sgs_farm:tea_mint", 60),
    ("小吃/半成品", "爆米花", "sgs_farm:popcorn", 75),
    ("小吃/半成品", "鸭腿", "sgs_farm:duck_drum_raw", 80),
    ("小吃/半成品", "蛤蜊", "sgs_farm:clam", 100),
    ("小吃/半成品", "虾", "sgs_farm:shrimp", 120),
    ("小吃/半成品", "培根", "sgs_farm:bacon", 115),
    ("小吃/半成品", "燕麦片", "sgs_farm:granola", 120),
    ("小吃/半成品", "番茄酱", "sgs_farm:sauce_tomato", 120),
    ("小吃/半成品", "草本茶", "sgs_farm:tea_herbal", 120),
    ("小吃/半成品", "生鸭肉", "sgs_farm:duck_raw", 160),
    ("小吃/半成品", "生火鸡肉", "sgs_farm:turkey_raw", 180),
    ("小吃/半成品", "草莓果酱", "sgs_farm:jam_strawberry", 245),
    ("小吃/半成品", "螃蟹", "sgs_farm:crab", 280),
    ("小吃/半成品", "紫菜片", "sgs_farm:nori", 265),
    ("小吃/半成品", "醋栗果酱", "sgs_farm:jam_gooseberry", 385),
    ("小吃/半成品", "蓝莓果酱", "sgs_farm:jam_blueberry", 415),
    ("小吃/半成品", "黑莓果酱", "sgs_farm:jam_blackberry", 445),
    ("小吃/半成品", "覆盆子果酱", "sgs_farm:jam_raspberry", 465),
    ("小吃/半成品", "青酱", "sgs_farm:pesto", 625),
    ("小吃/半成品", "龙虾", "sgs_farm:lobster", 840),
    # 简单料理
    ("简单料理", "奶油小面包", "sgs_farm:brioche_bun", 0),
    ("简单料理", "奶酪楔块", "sgs_farm:cheese", 4),
    ("简单料理", "玉米饼", "sgs_farm:tortillas", 0),
    ("简单料理", "面包条", "sgs_farm:bread_loaf", 0),
    ("简单料理", "煎蛋", "sgs_farm:egg_fried", 25),
    ("简单料理", "熟意面", "sgs_farm:pasta_cooked", 30),
    ("简单料理", "刺身", "sgs_farm:sashimi", 31),
    ("简单料理", "米饭", "sgs_farm:rice_cooked", 40),
    ("简单料理", "烤韭葱", "sgs_farm:leek_roasted", 80),
    ("简单料理", "烤胡萝卜", "sgs_farm:carrot_roasted", 105),
    ("简单料理", "浆果水果沙拉", "sgs_farm:salad_fruit_berry", 115),
    ("简单料理", "水果沙拉", "sgs_farm:salad_fruit", 135),
    ("简单料理", "握寿司", "sgs_farm:nigiri", 150),
    ("简单料理", "烤南瓜", "sgs_farm:squash_roasted", 160),
    ("简单料理", "烤欧洲防风草", "sgs_farm:parsnip_roasted", 170),
    ("简单料理", "花生酱", "sgs_farm:spread_peanut", 195),
    ("简单料理", "意大利辣香肠", "sgs_farm:pepperoni", 210),
    ("简单料理", "番茄意面", "sgs_farm:pasta_tomato", 220),
    ("简单料理", "山羊奶酪", "sgs_farm:cheese_goat", 240),
    ("简单料理", "菲达奶酪", "sgs_farm:cheese_feta", 240),
    ("简单料理", "马苏里拉奶酪", "sgs_farm:cheese_mozzarella", 240),
    ("简单料理", "山羊奶", "sgs_farm:milk_goat", 280),
    ("简单料理", "水牛奶", "sgs_farm:milk_buffalo", 280),
    ("简单料理", "牛奶", "sgs_farm:milk_cow", 280),
    ("简单料理", "绵羊奶", "sgs_farm:milk_sheep", 280),
    ("简单料理", "墨西哥蔬菜卷", "sgs_farm:veg_fajitas", 270),
    ("简单料理", "肉酱", "sgs_farm:sauce_bolognese", 285),
    ("简单料理", "黄瓜寿司卷", "sgs_farm:kappa_maki", 340),
    ("简单料理", "巧克力酱", "sgs_farm:spread_chocolate", 385),
    ("简单料理", "炒芦笋", "sgs_farm:asparagus_sauteed", 400),
    ("简单料理", "寿司卷", "sgs_farm:maki_roll", 415),
    ("简单料理", "烤洋蓟", "sgs_farm:artichoke_roasted", 440),
    ("简单料理", "牛油果寿司卷", "sgs_farm:avocado_maki", 470),
    ("简单料理", "热带水果沙拉", "sgs_farm:salad_fruit_trop", 495),
    ("简单料理", "香蒜酱意面", "sgs_farm:pasta_pesto", 725),
    # 饮品
    ("饮品", "梨汁", "sgs_farm:juice_pear", 120),
    ("饮品", "橙汁", "sgs_farm:juice_orange", 120),
    ("饮品", "苹果汁", "sgs_farm:juice_apple", 120),
    ("饮品", "柠檬汁", "sgs_farm:juice_lemon", 140),
    ("饮品", "桃子汁", "sgs_farm:juice_peach", 140),
    ("饮品", "菠萝汁", "sgs_farm:juice_pineapple", 160),
    ("饮品", "青柠汁", "sgs_farm:juice_lime", 160),
    ("饮品", "青葡萄汁", "sgs_farm:juice_grape_green", 160),
    ("饮品", "红葡萄汁", "sgs_farm:juice_grape_red", 170),
    ("饮品", "百香果汁", "sgs_farm:juice_passionfruit", 220),
    ("饮品", "红醋栗汁", "sgs_farm:juice_redcurrant", 340),
    ("饮品", "黑加仑汁", "sgs_farm:juice_blackcurrant", 340),
    ("饮品", "樱桃汁", "sgs_farm:juice_cherry", 490),
    # 正餐
    ("正餐", "土豆泥", "sgs_farm:potato_mashed", 0),
    ("正餐", "奶黄包", "sgs_farm:bun_custard", 0),
    ("正餐", "果酱包", "sgs_farm:bun_jelly", 0),
    ("正餐", "早餐麦片", "sgs_farm:cereal", 80),
    ("正餐", "豌豆汤", "sgs_farm:soup_pea", 80),
    ("正餐", "蛋炒饭", "sgs_farm:rice_fried_egg", 105),
    ("正餐", "土豆饼", "sgs_farm:potato_rosti", 125),
    ("正餐", "杂烩浓汤", "sgs_farm:chowder", 125),
    ("正餐", "熟牛肉饼", "sgs_farm:beef_patty", 125),
    ("正餐", "炸鸡柳", "sgs_farm:chicken_tender", 130),
    ("正餐", "蒜香面包", "sgs_farm:pizza_garlic", 145),
    ("正餐", "蘑菇炒饭", "sgs_farm:rice_fried_mushroom", 145),
    ("正餐", "炸虾薯条", "sgs_farm:scampi_chips", 165),
    ("正餐", "炸鱼薯条", "sgs_farm:fish_chips", 165),
    ("正餐", "炸鸡", "sgs_farm:chicken_fried", 182),
    ("正餐", "芝士韭葱", "sgs_farm:leek_cheese", 194),
    ("正餐", "蛋与豆苗三明治", "sgs_farm:sandwich_egg", 200),
    ("正餐", "芝士花椰菜", "sgs_farm:cauliflower_cheese", 214),
    ("正餐", "炸鸭", "sgs_farm:duck_fried", 225),
    ("正餐", "蔬菜汤", "sgs_farm:soup_vegatable", 230),
    ("正餐", "培根三明治", "sgs_farm:sandwich_bacon", 235),
    ("正餐", "培根煎蛋", "sgs_farm:bacon_eggs", 260),
    ("正餐", "意大利培根蛋面", "sgs_farm:pasta_carbonara", 265),
    ("正餐", "苹果脆皮甜点", "sgs_farm:crumble_apple", 265),
    ("正餐", "火腿沙拉三明治", "sgs_farm:sandwich_ham", 270),
    ("正餐", "草莓芭菲", "sgs_farm:parfait_strawberry", 285),
    ("正餐", "炖羊肉", "sgs_farm:stew_lamb", 310),
    ("正餐", "炖蔬菜", "sgs_farm:stew_veg", 325),
    ("正餐", "烤猪肉三明治", "sgs_farm:sandwich_pork", 340),
    ("正餐", "大黄脆皮甜点", "sgs_farm:crumble_rhubarb", 375),
    ("正餐", "意式蔬菜汤", "sgs_farm:soup_minestrone", 375),
    ("正餐", "意大利肉酱面", "sgs_farm:pasta_bolognese", 385),
    ("正餐", "蔬菜沙拉", "sgs_farm:salad_green", 400),
    ("正餐", "番茄沙拉", "sgs_farm:salad_tomato", 410),
    ("正餐", "烤菲达奶酪", "sgs_farm:feta_grilled", 420),
    ("正餐", "炸寿司卷", "sgs_farm:fried_maki", 440),
    ("正餐", "蓝莓芭菲", "sgs_farm:parfait_blueberry", 455),
    ("正餐", "熟蟹", "sgs_farm:crab_dressed", 500),
    ("正餐", "烤奶酪", "sgs_farm:cheese_oven", 600),
    ("正餐", "反卷寿司", "sgs_farm:uramaki", 645),
    ("正餐", "炸反卷寿司", "sgs_farm:fried_uramaki", 670),
    ("正餐", "普罗旺斯杂烩", "sgs_farm:ratatouille", 795),
    # 大餐
    ("大餐", "羊肉炖菜", "sgs_farm:lamb_ragu", 140),
    ("大餐", "玛格丽塔披萨", "sgs_farm:pizza_margherita", 190),
    ("大餐", "蘑菇汉堡", "sgs_farm:burger_mushroom", 220),
    ("大餐", "招牌炒饭", "sgs_farm:rice_fried_meat", 230),
    ("大餐", "玉米卷饼", "sgs_farm:enchilada", 225),
    ("大餐", "炒绿卷心菜", "sgs_farm:cabbage_sauteed", 265),
    ("大餐", "俄式炖牛肉", "sgs_farm:beef_stroganoff", 275),
    ("大餐", "炖牛肉", "sgs_farm:stew_beef", 285),
    ("大餐", "红酒炖鸡", "sgs_farm:stew_chicken", 295),
    ("大餐", "牛肉汉堡", "sgs_farm:burger_beef", 305),
    ("大餐", "鸡肉汉堡", "sgs_farm:burger_chicken", 310),
    ("大餐", "烤牛肉三明治", "sgs_farm:sandwich_beef", 320),
    ("大餐", "蔬菜炒饭", "sgs_farm:rice_fried_veg", 335),
    ("大餐", "玉米浓汤", "sgs_farm:soup_corn", 350),
    ("大餐", "培根生菜番茄三明治", "sgs_farm:sandwich_blt", 365),
    ("大餐", "羊腿", "sgs_farm:lamb_shanks", 360),
    ("大餐", "辣味牛肉杂烩", "sgs_farm:chili_con_carne", 395),
    ("大餐", "意大利辣香肠披萨", "sgs_farm:pizza_pepperoni", 400),
    ("大餐", "夏威夷披萨", "sgs_farm:pizza_hawaiian", 430),
    ("大餐", "红卷心菜炖菜", "sgs_farm:cabbage_braised", 425),
    ("大餐", "牛肉千层面", "sgs_farm:lasagne_beef", 454),
    ("大餐", "烤猪肉晚餐", "sgs_farm:roast_dinner_pork", 470),
    ("大餐", "卡普雷塞三明治", "sgs_farm:sandwich_caprese", 530),
    ("大餐", "卡普雷塞沙拉", "sgs_farm:salad_caprese", 550),
    ("大餐", "烤夏季蔬菜", "sgs_farm:roasted_veg_summer", 575),
    ("大餐", "蔬菜披萨", "sgs_farm:pizze_veg", 570),
    ("大餐", "蔬菜法士达卷饼", "sgs_farm:fajitas_veg", 570),
    ("大餐", "卡普雷塞披萨", "sgs_farm:pizza_caprese", 600),
    ("大餐", "烤鸭", "sgs_farm:duck_roast", 600),
    ("大餐", "烤冬季蔬菜", "sgs_farm:roasted_veg_winter", 605),
    ("大餐", "烤鸡晚餐", "sgs_farm:roast_dinner_chicken", 610),
    ("大餐", "烤火鸡", "sgs_farm:turkey_roast", 620),
    ("大餐", "砂锅菜", "sgs_farm:casserole", 615),
    ("大餐", "蔬菜千层面", "sgs_farm:lasagne_veg", 654),
    ("大餐", "鸡肉法士达卷饼", "sgs_farm:fajitas_chicken", 660),
    ("大餐", "尼斯沙拉", "sgs_farm:salad_nicoise", 720),
    ("大餐", "希腊沙拉", "sgs_farm:salad_greek", 750),
    ("大餐", "牛肉法士达卷饼", "sgs_farm:fajitas_beef", 830),
    ("大餐", "黄油龙虾", "sgs_farm:lobster_butter", 1180),
    ("大餐", "寿司拼盘", "sgs_farm:sushi_menu", 1726),
]

# (item_id, name, thirst_delta, va, vc, fe, pr)
VANILLA_FOODS = [
    ("minecraft:apple", "苹果", 6, 2, 12, 1, 1),
    ("minecraft:golden_apple", "金苹果", 8, 10, 15, 8, 8),
    ("minecraft:enchanted_golden_apple", "附魔金苹果", 10, 12, 18, 10, 10),
    ("minecraft:melon_slice", "西瓜片", 10, 1, 15, 1, 1),
    ("minecraft:sweet_berries", "甜浆果", 5, 2, 18, 1, 1),
    ("minecraft:glow_berries", "发光浆果", 4, 4, 10, 1, 2),
    ("minecraft:chorus_fruit", "紫颂果", 4, 3, 8, 1, 2),
    ("minecraft:carrot", "胡萝卜", 4, 25, 2, 1, 1),
    ("minecraft:golden_carrot", "金胡萝卜", 3, 40, 3, 2, 2),
    ("minecraft:potato", "土豆", 2, 2, 5, 1, 3),
    ("minecraft:baked_potato", "烤土豆", -4, 3, 6, 2, 4),
    ("minecraft:poisonous_potato", "毒土豆", -2, 0, 1, 0, 1),
    ("minecraft:beetroot", "甜菜根", 3, 3, 8, 2, 2),
    ("minecraft:beetroot_soup", "甜菜汤", 6, 5, 10, 3, 5),
    ("minecraft:mushroom_stew", "蘑菇煲", 5, 3, 6, 5, 8),
    ("minecraft:rabbit_stew", "兔肉煲", -3, 5, 8, 14, 16),
    ("minecraft:suspicious_stew", "迷之炖菜", 4, 4, 6, 3, 4),
    ("minecraft:bread", "面包", -5, 1, 3, 2, 6),
    ("minecraft:cookie", "曲奇", -6, 1, 2, 2, 3),
    ("minecraft:cake", "蛋糕", -8, 2, 3, 2, 4),
    ("minecraft:pumpkin_pie", "南瓜派", -5, 8, 5, 3, 5),
    ("minecraft:egg", "鸡蛋", -2, 4, 2, 4, 10),
    ("minecraft:beef", "生牛肉", -3, 1, 1, 10, 12),
    ("minecraft:cooked_beef", "熟牛肉", -8, 2, 2, 18, 20),
    ("minecraft:porkchop", "生猪排", -3, 1, 1, 9, 11),
    ("minecraft:cooked_porkchop", "熟猪排", -8, 2, 2, 16, 18),
    ("minecraft:mutton", "生羊肉", -3, 1, 1, 8, 10),
    ("minecraft:cooked_mutton", "熟羊肉", -8, 2, 2, 14, 16),
    ("minecraft:chicken", "生鸡肉", -3, 1, 2, 7, 10),
    ("minecraft:cooked_chicken", "熟鸡肉", -7, 2, 3, 12, 16),
    ("minecraft:cod", "生鳕鱼", -2, 2, 3, 6, 9),
    ("minecraft:cooked_cod", "熟鳕鱼", -6, 3, 4, 10, 14),
    ("minecraft:salmon", "生鲑鱼", -2, 2, 4, 7, 10),
    ("minecraft:cooked_salmon", "熟鲑鱼", -6, 3, 5, 12, 15),
    ("minecraft:tropical_fish", "热带鱼", -2, 2, 3, 5, 8),
    ("minecraft:pufferfish", "河豚", -10, 1, 2, 4, 6),
    ("minecraft:rotten_flesh", "腐肉", -12, 0, 0, 2, 8),
    ("minecraft:spider_eye", "蜘蛛眼", -8, 1, 1, 2, 3),
    ("minecraft:honey_bottle", "蜂蜜瓶", 5, 1, 2, 1, 1),
    ("minecraft:milk_bucket", "牛奶桶", 15, 4, 2, 2, 8),
    ("minecraft:dried_kelp", "干海带", -10, 2, 4, 8, 3),
    ("minecraft:kelp", "海带", 3, 2, 6, 4, 2),
    ("minecraft:fermented_spider_eye", "发酵蛛眼", -8, 0, 1, 1, 2),
    ("minecraft:glistering_melon_slice", "闪烁西瓜片", 8, 2, 20, 2, 2),
    ("minecraft:beetroot_seeds", "甜菜种子", 0, 0, 1, 0, 1),
]

DRY_KEYWORDS = (
    "bacon", "popcorn", "pepperoni", "chili", "powder", "flour", "jam",
    "spread_peanut", "spread_chocolate", "fried", "pizza", "burger", "chips",
    "wasabi", "ginger", "garlic", "crumble", "granola", "breadcrumbs",
)
WET_KEYWORDS = (
    "soup", "chowder", "stew", "juice", "milk", "tea", "salad", "melon_slice",
)
MEAT_KEYWORDS = (
    "beef", "chicken", "pork", "duck", "turkey", "lamb", "bacon", "pepperoni",
    "clam", "shrimp", "crab", "lobster", "fish", "sashimi", "nigiri", "maki",
    "sushi", "scampi", "patty", "burger", "ham",
)
FRUIT_KEYWORDS = (
    "fruit", "berry", "berries", "melon", "apple", "pear", "orange", "peach",
    "cherry", "grape", "lemon", "lime", "pineapple", "passion", "strawberr",
    "currant", "gooseberry", "blueberr", "blackberr", "raspberr", "avocado",
)
GREEN_KEYWORDS = (
    "lettuce", "spinach", "cabbage", "broccoli", "cauliflower", "salad", "veg",
    "asparagus", "artichoke", "watercress", "cress", "pak_choi", "leek",
)


def _tier_scale(tier: int) -> int:
    return max(1, min(20, tier // 40 + 1))


def _match_any(text: str, keywords: tuple[str, ...]) -> bool:
    t = text.lower()
    return any(k in t for k in keywords)


def compute_thirst(category: str, name: str, item_id: str, tier: int) -> int:
    iid = item_id.lower()
    scale = _tier_scale(tier)

    if category == "饮品" or "juice" in iid:
        return min(28, 10 + scale * 2)

    if "milk" in iid or iid.endswith(":custard"):
        return 12

    if category == "原料·农作物":
        base = 4 + scale
        if _match_any(iid, FRUIT_KEYWORDS):
            return min(12, base + 2)
        if "peppers_chili" in iid or "wasabi" in iid:
            return -8
        return min(10, base)

    if category == "原料·香草":
        return -2

    if category == "原料·加工":
        if "oil" in iid:
            return -6
        if _match_any(iid, ("powder", "flour", "chili")):
            return -10
        return -5

    if _match_any(iid, WET_KEYWORDS) or "tea" in iid:
        if "soup" in iid or "chowder" in iid or "stew" in iid:
            return min(8, 3 + scale // 2)
        if "tea" in iid:
            return 8
        if "salad" in iid:
            return 4 + scale // 2
        return 6 + scale // 2

    if _match_any(iid, DRY_KEYWORDS) or _match_any(name, ("培根", "爆米花", "披萨", "炸", "辣", "酱", "粉", "芥末")):
        return -min(15, 6 + scale)

    if category in ("正餐", "大餐", "简单料理"):
        penalty = 4 + scale
        if _match_any(iid, MEAT_KEYWORDS):
            penalty += 2
        if "pasta" in iid or "rice" in iid or "bread" in iid:
            penalty += 1
        return -min(16, penalty)

    if category == "小吃/半成品":
        if _match_any(iid, MEAT_KEYWORDS):
            return -min(10, 4 + scale)
        if "egg" in iid:
            return -3
        if "cream" in iid or "butter" in iid or "dough" in iid:
            return -2
        return -min(8, 3 + scale // 2)

    return 0


def compute_nutrition(category: str, name: str, item_id: str, tier: int) -> tuple[int, int, int, int]:
    iid = item_id.lower()
    scale = _tier_scale(tier)
    va = vc = fe = pr = 0

    if category == "原料·农作物":
        if _match_any(iid, FRUIT_KEYWORDS):
            vc = 6 + scale * 2
            va = 3 + scale
        elif _match_any(iid, GREEN_KEYWORDS) or any(k in iid for k in ("spinach", "broccoli", "tomato", "carrot")):
            va = 8 + scale * 2
            vc = 5 + scale
            fe = 1 + scale // 2
        elif any(k in iid for k in ("beans", "peas", "peanut")):
            pr = 4 + scale * 2
            fe = 2 + scale
        elif any(k in iid for k in ("rice", "rye", "maize", "corn")):
            pr = 3 + scale
        else:
            va = 2 + scale
            vc = 3 + scale
    elif category == "原料·香草":
        vc = 4 + scale * 2
    elif category == "原料·加工":
        if "cheese" in iid:
            pr = 4 + scale
            va = 2 + scale // 2
        else:
            pr = 2 + scale
    elif category == "饮品" or "juice" in iid:
        vc = 10 + scale * 2
        va = 3 + scale
    elif _match_any(iid, MEAT_KEYWORDS):
        pr = 10 + scale * 3
        fe = 5 + scale * 2
    elif "egg" in iid:
        pr = 6 + scale * 2
        va = 3
    elif "milk" in iid or "cheese" in iid or "cream" in iid:
        pr = 5 + scale * 2
        va = 3 + scale
        fe = 2 + scale // 2
    elif "mushroom" in iid:
        fe = 2 + scale
        pr = 2 + scale
    elif "nori" in iid:
        fe = 4 + scale
        va = 2
    elif "soup" in iid or "stew" in iid or "chowder" in iid:
        pr = 5 + scale * 2
        fe = 3 + scale
        vc = 4 + scale
    elif "salad" in iid:
        va = 8 + scale * 2
        vc = 10 + scale * 2
        fe = 2 + scale
    elif any(k in iid for k in ("pasta", "pizza", "bread", "rice", "cereal", "lasagne", "bun")):
        pr = 5 + scale * 2
        fe = 2 + scale
    elif "jam" in iid or "pesto" in iid:
        vc = 5 + scale
        pr = 2
    else:
        pr = 3 + scale
        vc = 2 + scale
        fe = 1 + scale // 2

    if "wasabi" in iid:
        vc = max(vc, 15)
    if "peppers_chili" in iid or "powder_chili" in iid:
        vc = max(vc, 8)

    cap = 25 if tier < 500 else 30
    return (
        min(cap, max(0, va)),
        min(cap, max(0, vc)),
        min(cap, max(0, fe)),
        min(cap, max(0, pr)),
    )


def ensure_tables(cur: sqlite3.Cursor) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS consume_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL UNIQUE,
            item_name TEXT,
            thirst_delta INTEGER NOT NULL DEFAULT 0,
            vitamin_a INTEGER NOT NULL DEFAULT 0,
            vitamin_c INTEGER NOT NULL DEFAULT 0,
            iron INTEGER NOT NULL DEFAULT 0,
            protein INTEGER NOT NULL DEFAULT 0,
            infection_delta INTEGER NOT NULL DEFAULT 0,
            buffs TEXT,
            show_toast INTEGER NOT NULL DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )


def upsert_food(cur: sqlite3.Cursor, item_id: str, name: str, thirst: int, nutrition: tuple[int, int, int, int], now: str) -> None:
    va, vc, fe, pr = nutrition
    # 只覆盖口渴/营养/名称；infection_delta、buffs、show_toast 保留表中现有值
    cur.execute(
        """
        INSERT INTO consume_items (item_id, item_name, thirst_delta, vitamin_a, vitamin_c, iron, protein, infection_delta, buffs, show_toast, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL, 0, ?, ?)
        ON CONFLICT(item_id) DO UPDATE SET
            item_name=excluded.item_name,
            thirst_delta=excluded.thirst_delta,
            vitamin_a=excluded.vitamin_a,
            vitamin_c=excluded.vitamin_c,
            iron=excluded.iron,
            protein=excluded.protein,
            updated_at=excluded.updated_at
        """,
        (item_id, name, thirst, va, vc, fe, pr, now, now),
    )


def seed(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    ensure_tables(cur)
    now = datetime.datetime.utcnow().isoformat()

    seen = set()
    count = 0
    for category, name, item_id, tier in SGS_FARM_FOODS:
        if item_id in seen:
            continue
        seen.add(item_id)
        thirst = compute_thirst(category, name, item_id, tier)
        nutrition = compute_nutrition(category, name, item_id, tier)
        upsert_food(cur, item_id, name, thirst, nutrition, now)
        count += 1

    for item_id, name, thirst, va, vc, fe, pr in VANILLA_FOODS:
        if item_id in seen:
            continue
        seen.add(item_id)
        upsert_food(cur, item_id, name, thirst, (va, vc, fe, pr), now)
        count += 1

    conn.commit()
    consume_n = cur.execute("SELECT COUNT(*) FROM consume_items").fetchone()[0]
    conn.close()
    print(f"Upserted {count} food rows into {db_path}")
    print(f"  consume_items={consume_n}")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB
    seed(target)
