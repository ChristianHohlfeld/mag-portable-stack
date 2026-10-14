// Minimal, dependency-free JSON value + parser/serializer.
// Only what the MAG room core needs: objects, arrays, strings, numbers, bools.
#pragma once
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <limits>
#include <map>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace mj {

struct Val;
using Arr = std::vector<Val>;
using Obj = std::vector<std::pair<std::string, Val>>;

struct Val {
  enum Type { NUL, BOOL, NUM, STR, ARR, OBJ } type = NUL;
  bool b = false;
  double num = 0;
  std::string str;
  std::shared_ptr<Arr> arr;
  std::shared_ptr<Obj> obj;

  Val() = default;
  Val(const Val &v)
      : type(v.type), b(v.b), num(v.num), str(v.str),
        arr(v.arr ? std::make_shared<Arr>(*v.arr) : nullptr),
        obj(v.obj ? std::make_shared<Obj>(*v.obj) : nullptr) {}
  Val(Val &&) noexcept = default;
  Val &operator=(Val &&) noexcept = default;
  Val &operator=(const Val &v) {
    if (this != &v) {
      Val copy(v);
      *this = std::move(copy);
    }
    return *this;
  }
  Val(bool v) : type(BOOL), b(v) {}
  Val(double v) : type(NUM), num(v) {}
  Val(int v) : type(NUM), num(v) {}
  Val(long v) : type(NUM), num((double)v) {}
  Val(const char *v) : type(STR), str(v) {}
  Val(const std::string &v) : type(STR), str(v) {}

  static Val object() {
    Val v;
    v.type = OBJ;
    v.obj = std::make_shared<Obj>();
    return v;
  }
  static Val array() {
    Val v;
    v.type = ARR;
    v.arr = std::make_shared<Arr>();
    return v;
  }

  bool isObj() const { return type == OBJ && obj; }
  bool isArr() const { return type == ARR && arr; }

  Val &operator[](const std::string &key) {
    if (!obj) {
      type = OBJ;
      obj = std::make_shared<Obj>();
    }
    for (auto &kv : *obj)
      if (kv.first == key)
        return kv.second;
    obj->emplace_back(key, Val());
    return obj->back().second;
  }
  const Val *find(const std::string &key) const {
    if (!obj)
      return nullptr;
    for (auto &kv : *obj)
      if (kv.first == key)
        return &kv.second;
    return nullptr;
  }
  Val *find(const std::string &key) {
    if (!obj)
      return nullptr;
    for (auto &kv : *obj)
      if (kv.first == key)
        return &kv.second;
    return nullptr;
  }
  void push(const Val &v) {
    if (!arr) {
      type = ARR;
      arr = std::make_shared<Arr>();
    }
    arr->push_back(v);
  }
  size_t size() const {
    if (isArr())
      return arr->size();
    if (isObj())
      return obj->size();
    return 0;
  }
  std::string asStr(const std::string &def = "") const {
    return type == STR ? str : def;
  }
  double asNum(double def = 0) const { return type == NUM ? num : def; }
  bool asBool(bool def = false) const { return type == BOOL ? b : def; }
  bool truthy() const {
    if (type == BOOL)
      return b;
    if (type == NUM)
      return num != 0;
    if (type == STR)
      return !str.empty();
    return false;
  }
  const Arr &asArr() const {
    static Arr empty;
    return isArr() ? *arr : empty;
  }
};

inline void escapeTo(const std::string &s, std::string &out) {
  out.push_back('"');
  for (unsigned char c : s) {
    switch (c) {
    case '"':
      out += "\\\"";
      break;
    case '\\':
      out += "\\\\";
      break;
    case '\n':
      out += "\\n";
      break;
    case '\r':
      out += "\\r";
      break;
    case '\t':
      out += "\\t";
      break;
    case '\b':
      out += "\\b";
      break;
    case '\f':
      out += "\\f";
      break;
    default:
      if (c < 0x20) {
        char buf[8];
        snprintf(buf, sizeof buf, "\\u%04x", c);
        out += buf;
      } else
        out.push_back((char)c);
    }
  }
  out.push_back('"');
}

inline void dumpTo(const Val &v, std::string &out) {
  switch (v.type) {
  case Val::NUL:
    out += "null";
    break;
  case Val::BOOL:
    out += v.b ? "true" : "false";
    break;
  case Val::NUM: {
    if (std::isfinite(v.num)) {
      if (v.num >= -9223372036854775808.0 && v.num < 9223372036854775808.0 &&
          v.num == (long long)v.num) {
        char buf[32];
        snprintf(buf, sizeof buf, "%lld", (long long)v.num);
        out += buf;
      } else {
        char buf[64];
        snprintf(buf, sizeof buf, "%.17g", v.num);
        out += buf;
      }
    } else
      out += "null";
    break;
  }
  case Val::STR:
    escapeTo(v.str, out);
    break;
  case Val::ARR: {
    out.push_back('[');
    bool first = true;
    for (auto &e : *v.arr) {
      if (!first)
        out.push_back(',');
      first = false;
      dumpTo(e, out);
    }
    out.push_back(']');
    break;
  }
  case Val::OBJ: {
    out.push_back('{');
    bool first = true;
    for (auto &kv : *v.obj) {
      if (!first)
        out.push_back(',');
      first = false;
      escapeTo(kv.first, out);
      out.push_back(':');
      dumpTo(kv.second, out);
    }
    out.push_back('}');
    break;
  }
  }
}

inline std::string dump(const Val &v) {
  std::string out;
  dumpTo(v, out);
  return out;
}

class Parser {
public:
  explicit Parser(const std::string &s)
      : p_(s.data()), end_(s.data() + s.size()) {}

  bool parse(Val &out) {
    skip();
    if (!value(out))
      return false;
    skip();
    return p_ == end_;
  }

private:
  const char *p_;
  const char *end_;
  unsigned depth_ = 0;

  void skip() {
    while (p_ < end_ &&
           (*p_ == ' ' || *p_ == '\t' || *p_ == '\n' || *p_ == '\r'))
      p_++;
  }

  bool value(Val &out) {
    skip();
    if (p_ >= end_)
      return false;
    char c = *p_;
    if (c == '{' || c == '[') {
      if (++depth_ > 128)
        return false;
      bool ok = c == '{' ? object(out) : array(out);
      --depth_;
      return ok;
    }
    if (c == '"') {
      out.type = Val::STR;
      return string(out.str);
    }
    if (c == 't' || c == 'f')
      return boolean(out);
    if (c == 'n') {
      if (end_ - p_ >= 4 && strncmp(p_, "null", 4) == 0) {
        p_ += 4;
        out.type = Val::NUL;
        return true;
      }
      return false;
    }
    return number(out);
  }

  bool object(Val &out) {
    out = Val::object();
    p_++; // {
    skip();
    if (p_ < end_ && *p_ == '}') {
      p_++;
      return true;
    }
    while (p_ < end_) {
      skip();
      std::string key;
      if (p_ >= end_ || *p_ != '"' || !string(key))
        return false;
      skip();
      if (p_ >= end_ || *p_ != ':')
        return false;
      p_++;
      Val v;
      if (!value(v))
        return false;
      out.obj->emplace_back(std::move(key), std::move(v));
      skip();
      if (p_ >= end_)
        return false;
      if (*p_ == ',') {
        p_++;
        continue;
      }
      if (*p_ == '}') {
        p_++;
        return true;
      }
      return false;
    }
    return false;
  }

  bool array(Val &out) {
    out = Val::array();
    p_++; // [
    skip();
    if (p_ < end_ && *p_ == ']') {
      p_++;
      return true;
    }
    while (p_ < end_) {
      Val v;
      if (!value(v))
        return false;
      out.arr->push_back(std::move(v));
      skip();
      if (p_ >= end_)
        return false;
      if (*p_ == ',') {
        p_++;
        continue;
      }
      if (*p_ == ']') {
        p_++;
        return true;
      }
      return false;
    }
    return false;
  }

  bool string(std::string &out) {
    out.clear();
    p_++; // opening quote
    while (p_ < end_) {
      char c = *p_++;
      if (c == '"')
        return true;
      if (c == '\\') {
        if (p_ >= end_)
          return false;
        char e = *p_++;
        switch (e) {
        case '"':
          out.push_back('"');
          break;
        case '\\':
          out.push_back('\\');
          break;
        case '/':
          out.push_back('/');
          break;
        case 'n':
          out.push_back('\n');
          break;
        case 't':
          out.push_back('\t');
          break;
        case 'r':
          out.push_back('\r');
          break;
        case 'b':
          out.push_back('\b');
          break;
        case 'f':
          out.push_back('\f');
          break;
        case 'u': {
          if (end_ - p_ < 4)
            return false;
          auto readHex = [&](unsigned &cp) {
            cp = 0;
            if (end_ - p_ < 4)
              return false;
            for (int i = 0; i < 4; ++i) {
              unsigned char h = *p_++;
              unsigned digit;
              if (h >= '0' && h <= '9')
                digit = h - '0';
              else if (h >= 'a' && h <= 'f')
                digit = h - 'a' + 10;
              else if (h >= 'A' && h <= 'F')
                digit = h - 'A' + 10;
              else
                return false;
              cp = cp * 16 + digit;
            }
            return true;
          };
          unsigned cp;
          if (!readHex(cp))
            return false;
          if (cp >= 0xD800 && cp <= 0xDBFF) {
            if (end_ - p_ < 6 || p_[0] != '\\' || p_[1] != 'u')
              return false;
            p_ += 2;
            unsigned low;
            if (!readHex(low) || low < 0xDC00 || low > 0xDFFF)
              return false;
            cp = 0x10000 + ((cp - 0xD800) << 10) + low - 0xDC00;
          } else if (cp >= 0xDC00 && cp <= 0xDFFF)
            return false;
          if (cp < 0x80)
            out.push_back((char)cp);
          else if (cp < 0x800) {
            out.push_back((char)(0xC0 | (cp >> 6)));
            out.push_back((char)(0x80 | (cp & 0x3F)));
          } else if (cp < 0x10000) {
            out.push_back((char)(0xE0 | (cp >> 12)));
            out.push_back((char)(0x80 | ((cp >> 6) & 0x3F)));
            out.push_back((char)(0x80 | (cp & 0x3F)));
          } else {
            out.push_back((char)(0xF0 | (cp >> 18)));
            out.push_back((char)(0x80 | ((cp >> 12) & 0x3F)));
            out.push_back((char)(0x80 | ((cp >> 6) & 0x3F)));
            out.push_back((char)(0x80 | (cp & 0x3F)));
          }
          break;
        }
        default:
          return false;
        }
      } else {
        if ((unsigned char)c < 0x20)
          return false;
        out.push_back(c);
      }
    }
    return false;
  }

  bool boolean(Val &out) {
    if (end_ - p_ >= 4 && strncmp(p_, "true", 4) == 0) {
      p_ += 4;
      out.type = Val::BOOL;
      out.b = true;
      return true;
    }
    if (end_ - p_ >= 5 && strncmp(p_, "false", 5) == 0) {
      p_ += 5;
      out.type = Val::BOOL;
      out.b = false;
      return true;
    }
    return false;
  }

  bool number(Val &out) {
    const char *start = p_;
    if (p_ < end_ && *p_ == '-')
      ++p_;
    if (p_ == end_)
      return false;
    if (*p_ == '0')
      ++p_;
    else if (*p_ >= '1' && *p_ <= '9') {
      do {
        ++p_;
      } while (p_ < end_ && std::isdigit((unsigned char)*p_));
    } else
      return false;
    if (p_ < end_ && *p_ == '.') {
      ++p_;
      const char *digits = p_;
      while (p_ < end_ && std::isdigit((unsigned char)*p_))
        ++p_;
      if (p_ == digits)
        return false;
    }
    if (p_ < end_ && (*p_ == 'e' || *p_ == 'E')) {
      ++p_;
      if (p_ < end_ && (*p_ == '+' || *p_ == '-'))
        ++p_;
      const char *digits = p_;
      while (p_ < end_ && std::isdigit((unsigned char)*p_))
        ++p_;
      if (p_ == digits)
        return false;
    }
    out.type = Val::NUM;
    out.num = std::strtod(std::string(start, p_).c_str(), nullptr);
    return std::isfinite(out.num);
  }
};

inline bool parse(const std::string &s, Val &out) {
  Parser p(s);
  return p.parse(out);
}

} // namespace mj
