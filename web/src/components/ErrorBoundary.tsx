import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button } from "./ui";

/**
 * 错误边界：React 里只有 class 组件能捕获子树渲染异常。
 *
 * 没有它的话，任何一个页面在渲染时抛错（比如接口返回的字段少了、
 * 数组当对象用），整站会直接白屏，用户连导航都点不了。
 * 这里只兜住「渲染期」错误，事件处理和异步错误仍需各页面自己 try/catch。
 */
export default class ErrorBoundary extends Component<
  { children: ReactNode; onReset?: () => void },
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 生产环境应上报到监控，这里至少保证控制台能定位到组件栈
    console.error("[ErrorBoundary]", error, info.componentStack);
  }

  reset = () => {
    this.setState({ error: null });
    this.props.onReset?.();
  };

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="panel p-8 max-w-xl mx-auto mt-10 text-center">
        <div className="text-[16.5px] text-ink">这个页面渲染出错了</div>
        <p className="text-[14px] text-muted mt-2 leading-relaxed">
          其他页面不受影响，可以重试一次；如果反复出现，多半是接口返回的数据结构和前端预期不一致。
        </p>
        <pre className="mt-3 text-left text-[13px] text-faint bg-paper border border-line rounded-lg p-3 overflow-auto max-h-40">
          {this.state.error.message}
        </pre>
        <div className="mt-4 flex justify-center gap-2">
          <Button variant="primary" onClick={this.reset}>
            重试
          </Button>
          <Button variant="ghost" onClick={() => window.location.reload()}>
            刷新页面
          </Button>
        </div>
      </div>
    );
  }
}
