
import os
import json
import time
import base64
import hashlib
import hmac
import requests

from decimal import Decimal
from pathlib import Path

# ==================== Comfly 配置 ====================

COMFLY_URL = "https://ai.comfly.org/api/user/self"
API_TOKEN = os.environ["API_TOKEN"]
NEW_API_USER = os.getenv("NEW_API_USER", "44031")

COMFLY_DIVISOR = Decimal(
    os.getenv("BALANCE_DIVISOR", "500000")
)
COMFLY_ALERT = Decimal(
    os.getenv("ALERT_THRESHOLD", "100")
)
COMFLY_RECOVERY = Decimal(
    os.getenv("RECOVERY_THRESHOLD", "110")
)

# ==================== 逆向狮配置 ====================

NIXIANGSHI_URL = "https://api.nixiangshi.com/openapi/v1/balance"
NIXIANGSHI_TOKEN = os.environ["NIXIANGSHI_API_TOKEN"]

NIXIANGSHI_ALERT = Decimal(
    os.getenv("NIXIANGSHI_ALERT_THRESHOLD", "1000")
)
NIXIANGSHI_RECOVERY = Decimal(
    os.getenv("NIXIANGSHI_RECOVERY_THRESHOLD", "1100")
)

# ==================== 通知配置 ====================

FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK", "")
FEISHU_SECRET = os.getenv("FEISHU_SECRET", "")
WEWORK_WEBHOOK = os.getenv("WEWORK_WEBHOOK", "")

STATE_FILE = Path("state.json")


def load_state():
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    else:
        state = {}

    # 兼容之前只有 alerted 字段的旧状态文件
    state.setdefault("comfly_alerted", state.get("alerted", False))
    state.setdefault("nixiangshi_alerted", False)

    return state


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def send_message(message):
    configured = bool(
        FEISHU_WEBHOOK or WEWORK_WEBHOOK
    )

    if not configured:
        raise RuntimeError("没有配置飞书或企业微信 Webhook")

    errors = []

    # 飞书通知
    if FEISHU_WEBHOOK:
        try:
            payload = {
                "msg_type": "text",
                "content": {"text": message}
            }

            if FEISHU_SECRET:
                timestamp = str(int(time.time()))
                string_to_sign = f"{timestamp}\n{FEISHU_SECRET}"

                sign = base64.b64encode(
                    hmac.new(
                        string_to_sign.encode("utf-8"),
                        digestmod=hashlib.sha256
                    ).digest()
                ).decode("utf-8")

                payload["timestamp"] = timestamp
                payload["sign"] = sign

            response = requests.post(
                FEISHU_WEBHOOK,
                json=payload,
                timeout=15
            )
            response.raise_for_status()

            result = response.json()
            if result.get("code", 0) != 0:
                raise RuntimeError(str(result))

            print("飞书通知成功")

        except Exception as e:
            errors.append(f"飞书发送失败: {e}")
            print(errors[-1])

    # 企业微信通知
    if WEWORK_WEBHOOK:
        try:
            response = requests.post(
                WEWORK_WEBHOOK,
                json={
                    "msgtype": "text",
                    "text": {"content": message}
                },
                timeout=15
            )
            response.raise_for_status()

            result = response.json()
            if result.get("errcode", 0) != 0:
                raise RuntimeError(str(result))

            print("企业微信通知成功")

        except Exception as e:
            errors.append(f"企业微信发送失败: {e}")
            print(errors[-1])

    if errors:
        raise RuntimeError("; ".join(errors))


def check_balance(
    name,
    balance,
    unit,
    alert_threshold,
    recovery_threshold,
    state_key,
    state
):
    if recovery_threshold <= alert_threshold:
        raise ValueError(
            f"{name}恢复阈值必须大于报警阈值"
        )

    alerted = state.get(state_key, False)

    print(f"{name}当前余额: {balance} {unit}")
    print(f"{name}报警阈值: {alert_threshold} {unit}")
    print(f"{name}恢复阈值: {recovery_threshold} {unit}")

    if balance <= alert_threshold and not alerted:
        message = (
            f"⚠️ {name}余额不足预警\n"
            f"当前余额：{balance} {unit}\n"
            f"报警阈值：{alert_threshold} {unit}\n"
            "请及时充值，避免影响 API 使用。"
        )

        send_message(message)
        state[state_key] = True
        save_state(state)

        print(f"{name}已触发报警")

    elif balance >= recovery_threshold and alerted:
        message = (
            f"✅ {name}余额已恢复\n"
            f"当前余额：{balance} {unit}\n"
            f"恢复阈值：{recovery_threshold} {unit}\n"
            "余额监控已解除报警状态。"
        )

        send_message(message)
        state[state_key] = False
        save_state(state)

        print(f"{name}已解除报警")

    elif alerted:
        print(f"{name}仍处于报警区间，不重复通知")

    else:
        print(f"{name}余额正常，无需报警")


def check_comfly(state):
    response = requests.get(
        COMFLY_URL,
        headers={
            "Authorization": f"Bearer {API_TOKEN}",
            "New-API-User": NEW_API_USER
        },
        timeout=20
    )
    response.raise_for_status()

    result = response.json()

    if not result.get("success"):
        raise RuntimeError(f"Comfly API 返回异常: {result.get('message')}")

    quota = Decimal(str(result["data"]["quota"]))
    balance = quota / COMFLY_DIVISOR

    print("\n========== Comfly ==========")
    print(f"原始 quota: {quota}")
    print(f"换算余额: ${balance:.2f}")

    check_balance(
        name="Comfly AI",
        balance=balance.quantize(Decimal("0.01")),
        unit="美元",
        alert_threshold=COMFLY_ALERT,
        recovery_threshold=COMFLY_RECOVERY,
        state_key="comfly_alerted",
        state=state
    )


def check_nixiangshi(state):
    response = requests.get(
        NIXIANGSHI_URL,
        headers={
            "Authorization": f"Bearer {NIXIANGSHI_TOKEN}"
        },
        timeout=20
    )
    response.raise_for_status()

    result = response.json()

    if result.get("code") != 1:
        raise RuntimeError(
            f"逆向狮 API 返回异常: {result.get('msg')}"
        )

    credits = Decimal(str(result["data"]["credits"]))

    print("\n========== 逆向狮 ==========")
    print(f"原始 credits: {credits}")

    check_balance(
        name="逆向狮",
        balance=credits,
        unit="积分",
        alert_threshold=NIXIANGSHI_ALERT,
        recovery_threshold=NIXIANGSHI_RECOVERY,
        state_key="nixiangshi_alerted",
        state=state
    )


def main():
    if COMFLY_DIVISOR <= 0:
        raise ValueError("Comfly 换算系数必须大于 0")

    state = load_state()
    errors = []

    # 两个平台独立执行，一个失败不影响另一个
    for name, checker in [
        ("Comfly", check_comfly),
        ("逆向狮", check_nixiangshi)
    ]:
        try:
            checker(state)
        except Exception as e:
            error = f"{name}监控失败: {e}"
            errors.append(error)
            print(error)

    if errors:
        raise RuntimeError("；".join(errors))

    print("\n所有余额监控执行完成")


if __name__ == "__main__":
    main()
