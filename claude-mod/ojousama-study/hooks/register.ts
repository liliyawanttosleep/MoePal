import type { Register, ThemeSetArgs } from 'claude-code'

import { makeColors } from './make-colors.js'

// Claude 大小姐：暖棕夜色的书房，橙色点缀。只做深色版（背景图是深色底）。
// Warm, lamp-lit study in the character's orange. Dark only: the backdrop picture has a dark ground.
const { theme, problems } = makeColors({
  label: 'Claude 大小姐',
  only: 'dark',
  roles: { accent: '#e8873a', danger: '#d9513f', success: '#6fa85a', warning: '#d9a233', pro: '#b98bd6' },
  dark: {
    gray: {
      anchors: {
        'surface-0': '#211813', // 必须和 art/ojousama.jpg 的底色一致
        'surface-1': '#2a1f19',
        'surface-2': '#33261f',
        'surface-3': '#3a2c24',
        'text-primary': '#f3e8d8',
        'text-secondary': '#d3c5b1',
        'text-muted': '#b0a08c',
      },
    },
  },
})

const LOOK: ThemeSetArgs = {
  colors: theme,
  backdrop: { image: { source: 'art/ojousama.jpg' } },
}

// 桌宠程序的位置；不在这台机器上时这一步会安静地失败。
const PET_DIR = 'D:\\MoePal'
const LAUNCH =
  `Start-Process -FilePath '${PET_DIR}\\.venv\\Scripts\\pythonw.exe' ` +
  `-ArgumentList '"${PET_DIR}\\MoePal.pyw"','--character=claude' -WorkingDirectory '${PET_DIR}'`

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const unpainted = problems.find(line => line.startsWith("Desktop won't paint"))

    if (unpainted !== undefined) {
      $.ui.toast(unpainted)
    }
    const set = await $.theme.set(LOOK)

    if (set.isDrawn) {
      $.ui.toast('🌸 Claude 大小姐已就位，今天也慢慢来吧。')
    } else {
      $.ui.status(set.reason)
    }

    // 顺手把桌面上的 Claude 大小姐叫出来（已经在运行就什么都不做：程序是单实例的）。
    try {
      await $.process.run(['powershell', '-NoProfile', '-WindowStyle', 'Hidden', '-Command', LAUNCH], {
        timeoutMs: 15000,
      })
    } catch {
      // 没装桌宠也不影响主题
    }

    return next(e)
  })
}
