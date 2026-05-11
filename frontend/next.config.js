/** @type {import('next').NextConfig} */
const nextConfig = {
  experimental: {
    turbo: {
      resolveAlias: {
        // 减少模块解析时间
      }
    }
  },
  compiler: {
    removeConsole: process.env.NODE_ENV === 'production'
  },
  swcMinify: true,
  // 启用增量构建
  generateBuildId: async () => {
    return 'build-' + Date.now()
  }
}

module.exports = nextConfig
